"""Harvest TwitterScore.io public scores for our known accounts (competitive research).

Scrapes each handle's profile page (ld+json) through a rotating Webshare proxy pool and
stores {user_id, username, name, twitterscore, band, followers} in twitterscore_accounts.
This gives a SECOND independent vendor signal over our smart-set M (Sorsa being the first).

    python -m scripts.harvest_twitterscore --pilot            # 50 handles, verify
    python -m scripts.harvest_twitterscore                    # full known universe
    python -m scripts.harvest_twitterscore --concurrency 40

Resumable: every attempted handle is checkpointed (incl. 404s, so they aren't retried).
Re-running scrapes only what's left. Logs go to stdout (Windows/PowerShell stderr gotcha).
Phase 2 (not here): expand the LIST via TS leaderboards + followedByList snowball.
"""
import argparse
import asyncio
import logging
import os
import sys

from sqlalchemy import select, func, case

from app.clients.twitterscore import TwitterScoreScraper, load_proxies
from app.core.config import settings
from app.db.models import SmartSetMember, TwitterScoreAccount
from app.db.session import SessionLocal, Base, engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    stream=sys.stdout)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("ts_harvest")

PROXY_FILE = r"C:\Users\mamoo\Downloads\Telegram Desktop\Webshare 100 proxies.txt"
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_DIR = os.path.join(_REPO_ROOT, "data", "twitterscore_state")
QUERIED = os.path.join(STATE_DIR, "queried.txt")
EXTRA_HANDLE_FILES = ["coingecko_handles.txt", "twitterscore_top100.txt",
                      "kaito_mindshare.txt", "vc_handles.txt"]

if settings.database_url.startswith("sqlite"):
    from sqlalchemy.dialects.sqlite import insert as _dialect_insert
else:  # pragma: no cover
    from sqlalchemy.dialects.postgresql import insert as _dialect_insert
_ROW_CHUNK = 70  # ~12 cols/row -> <=840 binds, under SQLite's 999 cap


def _load_set(path: str) -> set[str]:
    if not os.path.exists(path):
        return set()
    with open(path, encoding="utf-8") as f:
        return {ln.strip().lower() for ln in f if ln.strip()}


async def _all_handles() -> list[str]:
    """Union of handles to score: smart_set usernames + the extra seed handle files +
    accounts DISCOVERED via followedBy (so the new accounts get their category/description
    filled too, not just tags)."""
    seen: set[str] = set()
    out: list[str] = []
    async with SessionLocal() as s:
        rows = (await s.execute(
            select(SmartSetMember.username).where(SmartSetMember.username.isnot(None)))).scalars()
        for u in rows:
            k = u.lstrip("@").lower()
            if k and k not in seen:
                seen.add(k); out.append(u.lstrip("@"))
        # discovered TwitterScore accounts (e.g. from followedBy) not already covered
        trows = (await s.execute(
            select(TwitterScoreAccount.username).where(TwitterScoreAccount.username.isnot(None)))).scalars()
        for u in trows:
            k = u.lstrip("@").lower()
            if k and k not in seen:
                seen.add(k); out.append(u.lstrip("@"))
    for fn in EXTRA_HANDLE_FILES:
        p = os.path.join(_REPO_ROOT, "data", fn)
        if not os.path.exists(p):
            continue
        for ln in open(p, encoding="utf-8"):
            h = ln.strip().lstrip("@")
            if h and h.lower() not in seen:
                seen.add(h.lower()); out.append(h)
    return out


async def _upsert(rows: list[dict]) -> None:
    if not rows:
        return
    async with SessionLocal() as session:
        for i in range(0, len(rows), _ROW_CHUNK):
            stmt = _dialect_insert(TwitterScoreAccount).values(rows[i : i + _ROW_CHUNK])
            ex = stmt.excluded
            stmt = stmt.on_conflict_do_update(
                index_elements=[TwitterScoreAccount.user_id],
                set_={
                    "username": ex.username, "name": ex.name,
                    "twitterscore": ex.twitterscore, "band": ex.band,
                    "followers": ex.followers, "source": ex.source,
                    # account-level profile fields (don't null existing on a sparse re-run)
                    "description": func.coalesce(ex.description, TwitterScoreAccount.description),
                    "based_in": func.coalesce(ex.based_in, TwitterScoreAccount.based_in),
                    "joined_date": func.coalesce(ex.joined_date, TwitterScoreAccount.joined_date),
                    "renamed_count": func.coalesce(ex.renamed_count, TwitterScoreAccount.renamed_count),
                    # don't overwrite an existing category with an empty scrape
                    "categories": func.coalesce(func.nullif(ex.categories, ""), TwitterScoreAccount.categories),
                    # NOTE: tags/smart_followers come from followedBy — left untouched here
                },
            )
            await session.execute(stmt)
        await session.commit()


def _chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pilot", action="store_true", help="first 50 handles, verify")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--concurrency", type=int, default=30)
    args = ap.parse_args()

    async with engine.begin() as c:        # ensure twitterscore_accounts exists
        await c.run_sync(Base.metadata.create_all)
    os.makedirs(STATE_DIR, exist_ok=True)

    scraper = TwitterScoreScraper(load_proxies(PROXY_FILE))
    done = _load_set(QUERIED)
    handles = [h for h in await _all_handles() if h.lower() not in done]
    if args.pilot:
        handles = handles[:50]
    elif args.limit:
        handles = handles[: args.limit]
    logger.info("ts harvest: %d handles to scrape (%d already done), concurrency=%d",
                len(handles), len(done), args.concurrency)

    found = none = 0
    qf = open(QUERIED, "a", encoding="utf-8")
    try:
        for ci, chunk in enumerate(_chunks(handles, args.concurrency)):
            results = await asyncio.gather(*[scraper.fetch_profile(h) for h in chunk])
            rows = []
            for h, d in zip(chunk, results):
                qf.write(h.lower() + "\n")
                if d and d.get("user_id"):
                    rows.append({"user_id": d["user_id"], "username": d.get("username") or h,
                                 "name": d.get("name"), "twitterscore": d.get("twitterscore"),
                                 "band": d.get("band"), "followers": d.get("followers"),
                                 "description": d.get("description"),
                                 "categories": d.get("categories") or [],
                                 "based_in": d.get("based_in"), "joined_date": d.get("joined_date"),
                                 "renamed_count": d.get("renamed_count"), "source": "profile"})
                    found += 1
                else:
                    none += 1
            qf.flush()
            await _upsert(rows)
            if (ci + 1) % 5 == 0 or ci == 0:
                logger.info("  %d/%d scraped (found=%d none/404=%d)",
                            min((ci + 1) * args.concurrency, len(handles)), len(handles), found, none)
    finally:
        qf.close()

    async with SessionLocal() as s:
        total = (await s.execute(select(func.count()).select_from(TwitterScoreAccount))).scalar()
    print(f"\n=== ts harvest done ===  scraped={len(handles)} found={found} none/404={none} "
          f"| twitterscore_accounts table now {total} rows")


if __name__ == "__main__":
    asyncio.run(main())
