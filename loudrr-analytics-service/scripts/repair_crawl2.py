"""Repair truncated follow-graphs. Two phases, cheapest-first.

PHASE 1 (FREE): we already store a `following` count for 33,606 members (ranked_accounts +
smart_set.following_count). Audit those with ZERO api calls. This alone found 584 truncated
members missing 2,827,997 edges.

PHASE 2 (paid, optional): the other ~64.7k members have no stored profile, so their real
following count must be fetched. Uses the SINGLE-id endpoint (/user/info?userId=) — the
gateway's batch endpoint 503s constantly and is unusable for bulk.

Lesson baked in: do NOT wrap the client's own retry logic in another retry loop. The previous
version stacked 4 retries on top of tenacity's, so each flaky chunk took minutes and the whole
audit silently hung. The client already retries; here we just let a failure be a failure.

    python -m scripts.repair_crawl2 --audit-only     # report, change nothing
    python -m scripts.repair_crawl2                  # repair what phase 1 found
    python -m scripts.repair_crawl2 --with-unknowns  # + fetch counts for the 64.7k
"""
from __future__ import annotations

import asyncio
import csv
import logging
import os
import sys

sys.path.insert(0, ".")

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from app.clients.twitterapi import TwitterAPIClient  # noqa: E402
from app.services.crawl import _flush_edges, _mark_crawled  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    stream=sys.stdout)
log = logging.getLogger("repair2")
logging.getLogger("httpx").setLevel(logging.WARNING)

COMPLETE_AT = float(os.environ.get("COMPLETE_AT") or "0.90")
MIN_MISSING = int(os.environ.get("MIN_MISSING") or "100")
from app.clients.twitterapi import MAX_GATEWAY_CONCURRENCY  # noqa: E402

# Clamped to the client's hard ceiling: concurrency 32 here drained the gateway's shared
# 45-account pool and took the service down for every client (2026-07-14). The gateway now
# advertises ratelimit-* headers and the client self-throttles on them, but the cap stays.
REPAIR_CONC = min(int(os.environ.get("REPAIR_CONC") or "4"), MAX_GATEWAY_CONCURRENCY)
COUNT_CONC = min(int(os.environ.get("COUNT_CONC") or "6"), MAX_GATEWAY_CONCURRENCY)

REPAIRED_CSV = "data/exports/crawl_repaired.csv"


async def ensure_counts(eng) -> None:
    """Materialize stored-edge counts per member (one scan) — reused across phases."""
    async with eng.begin() as c:
        exists = (await c.execute(text(
            "select count(*) from information_schema.tables where table_name='crawl_counts'"
        ))).scalar()
        if exists:
            n = (await c.execute(text("select count(*) from crawl_counts"))).scalar()
            if n and n > 90000:
                log.info("crawl_counts already materialized (%s members)", f"{n:,}")
                return
        log.info("materializing per-member edge counts (one scan of the edge graph)...")
        await c.execute(text("set work_mem='512MB'"))
        await c.execute(text("DROP TABLE IF EXISTS crawl_counts"))
        await c.execute(text("""
            CREATE TABLE crawl_counts AS
            SELECT s.user_id, s.username, COALESCE(e.n,0)::bigint AS stored
            FROM smart_set s
            LEFT JOIN (SELECT follower_id, COUNT(*) n FROM edges GROUP BY follower_id) e
                   ON e.follower_id = s.user_id"""), execution_options={"timeout": 7200})
        await c.execute(text("ALTER TABLE crawl_counts ADD PRIMARY KEY (user_id)"))


async def fetch_unknown_counts(eng, tw) -> None:
    """Get real following counts for members we have no stored profile for. Persisted (pay-once).

    Throughput is entirely down to how we talk to the gateway's flaky batch endpoint. Measured
    on real ids:
        chunk=20 conc=16 -> 13 members/sec (100 failures)
        chunk=20 conc=32 -> 87 members/sec (ZERO failures)   <-- use this
        chunk=30 conc=32 ->  0 members/sec (total failure)
        single-id endpoint, conc=16 -> 0.5/sec (would take 36 HOURS)
    So: chunk exactly 20, high concurrency, and a plain httpx client (the shared TwitterAPIClient
    wraps every call in tenacity retries, which amplified the gateway's 503s into a silent hang).
    """
    async with eng.begin() as c:
        await c.execute(text("""
            CREATE TABLE IF NOT EXISTS crawl_real_following (
                user_id varchar(32) PRIMARY KEY, following bigint,
                fetched_at timestamp DEFAULT now())"""))
    async with eng.connect() as c:
        todo = [str(r[0]) for r in (await c.execute(text("""
            SELECT cc.user_id FROM crawl_counts cc
            JOIN smart_set s ON s.user_id = cc.user_id
            LEFT JOIN ranked_accounts r ON r.user_id = cc.user_id
            LEFT JOIN crawl_real_following f ON f.user_id = cc.user_id
            WHERE f.user_id IS NULL
              AND COALESCE(s.following_count, r.following) IS NULL
            ORDER BY cc.stored DESC"""))).all()]
    log.info("members with no known following count: %s", f"{len(todo):,}")
    if not todo:
        return

    import httpx

    from app.core.config import settings
    base = settings.gateway_base_url.rstrip("/") + "/twitter"
    hdr = {"x-api-key": settings.loudrr_gateway_api}

    CHUNK, CONC = 20, COUNT_CONC          # 20/32 is the measured sweet spot
    sem = asyncio.Semaphore(CONC)
    buf: list[dict] = []
    ok = err = 0

    budget = {"remaining": None, "reset": 60}
    # ONE global backoff: when the gateway says "slow down", every worker waits — otherwise
    # the other 5 keep hammering while one sleeps, and the 429s never clear.
    backoff_lock = asyncio.Lock()

    def retry_after(resp) -> float:
        ra = resp.headers.get("retry-after")
        if ra and str(ra).replace(".", "", 1).isdigit():
            return min(60.0, float(ra))
        return 5.0

    async def one(client, ch):
        nonlocal ok, err
        async with sem:
            # honour the gateway's advertised budget — it added these headers after we
            # exhausted its aux-account pool; a 503 here means we're hurting every client
            if budget["remaining"] is not None and budget["remaining"] <= 200:
                await asyncio.sleep(max(1, budget["reset"]))
                budget["remaining"] = None
            try:
                r = await client.get(f"{base}/user/batch_info_by_ids",
                                     params={"userIds": ",".join(ch)})
                rem = r.headers.get("ratelimit-remaining")
                if rem is not None:
                    budget["remaining"] = int(rem)
                    budget["reset"] = int(r.headers.get("ratelimit-reset") or 60)
                if r.status_code in (429, 503):
                    # 429 = "too many concurrent requests for your key" (the gateway's new
                    # per-key concurrency guard); 503 = aux pool exhausted. Neither is a
                    # failure to record — it means BACK OFF. Treating them as generic errors
                    # is what produced a 97% error rate: we sprayed doomed requests instead of
                    # slowing down. Retry this chunk after a pause; the caller re-runs to fill
                    # any gaps.
                    async with backoff_lock:
                        await asyncio.sleep(retry_after(r))
                    for _ in range(3):
                        r = await client.get(f"{base}/user/batch_info_by_ids",
                                             params={"userIds": ",".join(ch)})
                        if r.status_code == 200:
                            break
                        async with backoff_lock:
                            await asyncio.sleep(retry_after(r))
                    if r.status_code != 200:
                        err += len(ch)
                        return
                elif r.status_code != 200:
                    err += len(ch)
                    return
                for u in (r.json().get("users") or []):
                    uid = str(u.get("id") or "")
                    f = u.get("following")
                    if uid and f is not None:
                        buf.append({"u": uid, "f": int(f)})
                        ok += 1
            except Exception:  # noqa: BLE001 — a bad chunk is fine; we re-run to fill gaps
                err += len(ch)

    async def flush():
        if not buf:
            return
        rows = list(buf)
        buf.clear()
        async with eng.begin() as c:
            await c.execute(text("""
                insert into crawl_real_following (user_id, following) values (:u,:f)
                on conflict (user_id) do update set following=excluded.following"""), rows)

    chunks = [todo[i:i + CHUNK] for i in range(0, len(todo), CHUNK)]
    async with httpx.AsyncClient(timeout=30, headers=hdr,
                                 limits=httpx.Limits(max_connections=CONC)) as client:
        ROUND = CONC * 8
        for i in range(0, len(chunks), ROUND):
            await asyncio.gather(*(one(client, ch) for ch in chunks[i:i + ROUND]))
            await flush()
            log.info("  counts %s/%s (ok=%s err=%s)",
                     f"{min((i+ROUND)*CHUNK, len(todo)):,}", f"{len(todo):,}",
                     f"{ok:,}", f"{err:,}")
    await flush()


async def find_truncated(eng) -> list[tuple]:
    """Everyone whose stored edges fall short of their real following count."""
    async with eng.connect() as c:
        rows = (await c.execute(text(f"""
            SELECT cc.user_id, cc.username, cc.stored,
                   COALESCE(s.following_count, r.following, f.following) AS real
            FROM crawl_counts cc
            JOIN smart_set s ON s.user_id = cc.user_id
            LEFT JOIN ranked_accounts r ON r.user_id = cc.user_id
            LEFT JOIN crawl_real_following f ON f.user_id = cc.user_id
            WHERE COALESCE(s.following_count, r.following, f.following) > 0
              AND cc.stored < COALESCE(s.following_count, r.following, f.following) * {COMPLETE_AT}
              AND COALESCE(s.following_count, r.following, f.following) - cc.stored >= {MIN_MISSING}
            ORDER BY (COALESCE(s.following_count, r.following, f.following) - cc.stored) DESC"""))).all()
    return [(str(r[0]), r[1], int(r[2]), int(r[3])) for r in rows]


async def repair(eng, tw, broken: list[tuple]) -> None:
    sem = asyncio.Semaphore(REPAIR_CONC)
    done = {"n": 0, "edges": 0}
    os.makedirs("data/exports", exist_ok=True)
    fh = open(REPAIRED_CSV, "a", newline="", encoding="utf-8")
    w = csv.writer(fh)

    async def one(uid, uname, stored, real):
        async with sem:
            try:
                ids: list[str] = []
                async for fid in tw.iter_following_ids(uid, max_items=None):
                    ids.append(fid)
                await _flush_edges(uid, ids)
                await _mark_crawled(uid)
                async with eng.connect() as c:
                    now = (await c.execute(text(
                        "select count(*) from edges where follower_id=:u"), {"u": uid})).scalar()
                gained = now - stored
                done["n"] += 1
                done["edges"] += gained
                w.writerow([uid, uname, stored, now, real, gained])
                fh.flush()
                if done["n"] % 10 == 0 or gained > 5000:
                    log.info("  [%s/%s] @%-18s %s -> %s (+%s)  total +%s edges, $%.2f",
                             done["n"], len(broken), uname or uid, f"{stored:,}", f"{now:,}",
                             f"{gained:,}", f"{done['edges']:,}", getattr(tw, "usd_spent", 0.0))
            except Exception as e:  # noqa: BLE001
                log.warning("  @%s failed: %s", uname or uid, type(e).__name__)

    log.info("repairing %s truncated members (concurrency %s)...", f"{len(broken):,}", REPAIR_CONC)
    for i in range(0, len(broken), 50):
        await asyncio.gather(*(one(*b) for b in broken[i:i + 50]))
    fh.close()
    log.info("REPAIR DONE: %s members, +%s edges recovered, spent $%.2f",
             f"{done['n']:,}", f"{done['edges']:,}", getattr(tw, "usd_spent", 0.0))


async def main() -> None:
    eng = create_async_engine(os.environ["DATABASE_URL"],
                              connect_args={"command_timeout": 7200},
                              pool_pre_ping=True, pool_recycle=300)
    tw = TwitterAPIClient()
    await ensure_counts(eng)

    if "--with-unknowns" in sys.argv:
        await fetch_unknown_counts(eng, tw)

    broken = await find_truncated(eng)
    total_missing = sum(b[3] - b[2] for b in broken)
    log.info("=" * 62)
    log.info("TRUNCATED members: %s", f"{len(broken):,}")
    log.info("missing edges    : %s", f"{total_missing:,}")
    log.info("repair cost est. : $%.2f", total_missing / 1000 * 0.0049)
    log.info("=" * 62)
    for uid, uname, stored, real in broken[:12]:
        log.info("  @%-20s %9s / %-9s (%4.1f%%)  missing %s", uname or uid,
                 f"{stored:,}", f"{real:,}", stored / real * 100, f"{real-stored:,}")

    if "--audit-only" in sys.argv:
        log.info("audit-only: not repairing")
    elif broken:
        await repair(eng, tw, broken)
    await eng.dispose()


if __name__ == "__main__":
    asyncio.run(main())
