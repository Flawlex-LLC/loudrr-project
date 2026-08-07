"""TwitterScore SNOWBALL — recursively discover accounts + tags + edges via followedByList.

Start from our known accounts; for each, pull its significant followers across 5 category
filters (beats the anon 10-cap). Every follower NOT already seen is a NEW account and gets
enqueued, so the frontier expands until discovery saturates. Each record is VALIDATED
(validate_account) before it's stored — bad/mis-parsed rows are skipped + counted, never
stored, never killing the run. DB-only (no CSV).

Stops automatically on: (1) saturation soft-stop (new-per-account avg < threshold over a
window), (2) frontier drains (finite universe, every account processed once), or (3) the
--cap safety ceiling.

    python -m scripts.harvest_twitterscore_followedby --pilot              # ~80 accounts
    python -m scripts.harvest_twitterscore_followedby --cap 30000 --concurrency 80

Resumable: processed account ids are checkpointed. Logs -> stdout. Routes via proxy pool.
"""
import argparse
import asyncio
import logging
import os
import sys
from collections import deque

from sqlalchemy import select, func

from app.clients.twitterscore import TwitterScoreScraper, load_proxies, clean_account
from app.core.config import settings
from app.db.models import SmartSetMember, TwitterScoreAccount, TwitterScoreFollow
from app.db.session import SessionLocal, Base, engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    stream=sys.stdout)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("ts_snowball")

PROXY_FILE = r"C:\Users\mamoo\Downloads\Telegram Desktop\Webshare 100 proxies.txt"
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_DIR = os.path.join(_REPO_ROOT, "data", "twitterscore_state")
QUERIED = os.path.join(STATE_DIR, "snowball_processed.txt")

CATS = [("all", "All"), ("1", "Tier 1 VC"), ("2", "Tier 2 VC"),
        ("3", "Ecosystems"), ("4", "Other")]

if settings.database_url.startswith("sqlite"):
    from sqlalchemy.dialects.sqlite import insert as _dialect_insert
else:  # pragma: no cover
    from sqlalchemy.dialects.postgresql import insert as _dialect_insert
_CHUNK = 80
_FLUSH = 600  # buffered account rows before a DB commit


def _load_set(path: str) -> set[str]:
    if not os.path.exists(path):
        return set()
    with open(path, encoding="utf-8") as f:
        return {ln.strip() for ln in f if ln.strip()}


async def _store(accounts: list[dict], edges: list[dict]) -> None:
    acc = {a["user_id"]: a for a in accounts if a.get("user_id")}
    edg = {(e["followee_id"], e["follower_id"]): e for e in edges
           if e.get("followee_id") and e.get("follower_id")}
    async with SessionLocal() as session:
        for batch in (list(acc.values())[i:i+_CHUNK] for i in range(0, len(acc), _CHUNK)):
            stmt = _dialect_insert(TwitterScoreAccount).values(batch)
            ex = stmt.excluded
            stmt = stmt.on_conflict_do_update(
                index_elements=[TwitterScoreAccount.user_id],
                set_={
                    "username": func.coalesce(ex.username, TwitterScoreAccount.username),
                    "name": func.coalesce(ex.name, TwitterScoreAccount.name),
                    "twitterscore": func.coalesce(ex.twitterscore, TwitterScoreAccount.twitterscore),
                    "followers": func.coalesce(ex.followers, TwitterScoreAccount.followers),
                    "smart_followers": func.coalesce(ex.smart_followers, TwitterScoreAccount.smart_followers),
                    "tags": func.coalesce(func.nullif(ex.tags, ""), TwitterScoreAccount.tags),
                    "source": ex.source,
                })
            await session.execute(stmt)
        for batch in (list(edg.values())[i:i+_CHUNK] for i in range(0, len(edg), _CHUNK)):
            await session.execute(
                _dialect_insert(TwitterScoreFollow).values(batch).on_conflict_do_nothing(
                    index_elements=[TwitterScoreFollow.followee_id, TwitterScoreFollow.follower_id]))
        await session.commit()


async def _seed(processed: set[str]):
    """All known accounts -> (frontier of those not yet processed, seen-set of all ids)."""
    async with SessionLocal() as s:
        sm = (await s.execute(select(SmartSetMember.user_id, SmartSetMember.username)
                              .where(SmartSetMember.username.isnot(None)))).all()
        ts = (await s.execute(select(TwitterScoreAccount.user_id, TwitterScoreAccount.username)
                              .where(TwitterScoreAccount.username.isnot(None)))).all()
    seen: set[str] = set()
    frontier: list[tuple[str, str]] = []
    for uid, un in list(sm) + list(ts):
        if uid in seen:
            continue
        seen.add(uid)
        if uid not in processed and un:
            frontier.append((uid, str(un).lstrip("@")))
    return frontier, seen


async def snowball(scraper, frontier0, seen, processed, *, cap, acct_conc,
                   soft_window, soft_thresh, min_recurse_score):
    frontier: deque = deque(frontier0)
    seed_size = len(frontier)   # process ALL known accounts before judging saturation
    st = {"processed": 0, "valid": 0, "repaired": 0, "dropped": 0, "new": 0, "edges": 0}
    recent_new: deque = deque(maxlen=soft_window)
    stop = asyncio.Event()
    state_lock = asyncio.Lock()
    db_lock = asyncio.Lock()
    buf_acc: list[dict] = []
    buf_edge: list[dict] = []
    active = 0
    qf = open(QUERIED, "a", encoding="utf-8")

    async def flush():
        nonlocal buf_acc, buf_edge
        async with db_lock:
            if buf_acc or buf_edge:
                a, e = buf_acc, buf_edge
                buf_acc, buf_edge = [], []
                await _store(a, e)

    async def worker():
        nonlocal active, buf_acc, buf_edge
        while not stop.is_set():
            if frontier:
                uid, un = frontier.popleft()
            else:
                if active == 0:
                    return
                await asyncio.sleep(0.4)
                continue
            if uid in processed or not un:
                continue
            active += 1
            try:
                results = await asyncio.gather(
                    *[scraper.fetch_followed_by(uid, un, category_id=c, category_name=n)
                      for c, n in CATS])
            except Exception as e:  # noqa: BLE001 - one account's failure never kills the run
                logger.warning("skip %s: %s", un, e)
                active -= 1
                continue
            accts, edges, discovered = [], [], []
            for (c, n), rows in zip(CATS, results):
                for r in rows:
                    rec = {"user_id": r.get("user_id"), "username": r.get("username"),
                           "name": r.get("name"), "twitterscore": r.get("twitterscore"),
                           "tags": r.get("tags") or "", "smart_followers": r.get("smart_followers"),
                           "followers": r.get("followers"), "source": "followedby"}
                    cleaned, issues = clean_account(rec)
                    if cleaned is None:        # only no-id placeholders are dropped
                        st["dropped"] += 1
                        continue
                    if issues:
                        st["repaired"] += 1
                    st["valid"] += 1
                    accts.append(cleaned)
                    edges.append({"followee_id": uid, "follower_id": cleaned["user_id"],
                                  "category": n, "following_date": r.get("following_date")})
                    discovered.append((cleaned["user_id"], cleaned["username"],
                                       cleaned.get("twitterscore")))
            async with state_lock:
                if uid not in processed:
                    processed.add(uid)
                    qf.write(uid + "\n"); qf.flush()
                    st["processed"] += 1
                new_this = 0
                for fid, fun, fsc in discovered:
                    if fid and fid not in seen:
                        seen.add(fid); new_this += 1          # discovered (counts for saturation)
                        # RECURSE only through quality accounts; low/unscored are stored
                        # (already buffered) but not chased further
                        if fun and fsc is not None and fsc >= min_recurse_score:
                            frontier.append((fid, fun))
                st["new"] += new_this
                st["edges"] += len(edges)
                recent_new.append(new_this)
                buf_acc.extend(accts); buf_edge.extend(edges)
                if st["processed"] >= cap:
                    stop.set()
                # only judge saturation AFTER the full known-seed frontier is drained —
                # early mega-accounts have all-known followers (0 new) and would trip it falsely
                elif (st["processed"] >= seed_size and len(recent_new) == soft_window
                      and sum(recent_new) / soft_window < soft_thresh):
                    logger.info("saturation soft-stop: <%.3f new/acct over last %d (after seed drained)",
                                soft_thresh, soft_window)
                    stop.set()
                if st["processed"] % 200 == 0:
                    logger.info("  processed=%d valid=%d repaired=%d dropped=%d new_found=%d edges=%d frontier=%d",
                                st["processed"], st["valid"], st["repaired"], st["dropped"],
                                st["new"], st["edges"], len(frontier))
                do_flush = len(buf_acc) >= _FLUSH
            if do_flush:
                await flush()
            active -= 1
        return

    await asyncio.gather(*[worker() for _ in range(acct_conc)])
    await flush()
    qf.close()
    return st


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pilot", action="store_true", help="cap 80, verify accuracy")
    ap.add_argument("--cap", type=int, default=30000, help="max accounts to process (safety)")
    ap.add_argument("--concurrency", type=int, default=80, help="total in-flight requests")
    ap.add_argument("--soft-window", type=int, default=800, dest="soft_window")
    ap.add_argument("--soft-thresh", type=float, default=0.02, dest="soft_thresh",
                    help="stop when new-accounts/processed avg drops below this over the window")
    ap.add_argument("--min-recurse-score", type=float, default=100.0, dest="min_recurse_score",
                    help="only follow (recurse through) accounts with TwitterScore >= this; "
                         "lower-scored accounts are still stored, just not chased")
    args = ap.parse_args()

    async with engine.begin() as c:
        await c.run_sync(Base.metadata.create_all)
    os.makedirs(STATE_DIR, exist_ok=True)

    scraper = TwitterScoreScraper(load_proxies(PROXY_FILE), timeout=20.0)
    processed = _load_set(QUERIED)
    frontier, seen = await _seed(processed)
    cap = 80 if args.pilot else args.cap
    acct_conc = max(1, args.concurrency // len(CATS))
    logger.info("snowball: frontier=%d seen=%d processed=%d cap=%d concurrency=%d(=%d accts)",
                len(frontier), len(seen), len(processed), cap, args.concurrency, acct_conc)

    logger.info("recurse only through accounts with TwitterScore >= %s (lower stored, not chased)",
                args.min_recurse_score)
    st = await snowball(scraper, frontier, seen, processed, cap=cap, acct_conc=acct_conc,
                        soft_window=(50 if args.pilot else args.soft_window),
                        soft_thresh=args.soft_thresh, min_recurse_score=args.min_recurse_score)

    async with SessionLocal() as s:
        tot = (await s.execute(select(func.count()).select_from(TwitterScoreAccount))).scalar()
        edges = (await s.execute(select(func.count()).select_from(TwitterScoreFollow))).scalar()
    print(f"\n=== snowball done ===  {st}")
    print(f"twitterscore_accounts={tot}  twitterscore_follows(edges)={edges}")


if __name__ == "__main__":
    asyncio.run(main())
