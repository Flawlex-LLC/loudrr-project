"""Audit crawl completeness: for EVERY smart-set member, compare the edges we stored against
their REAL following count on X. Finds truncated/incomplete crawls.

Why this exists: @JohnCena's crawl silently stopped at 55,000 of his 1,058,213 following. It
was only caught because a founder noticed he was missing from someone's followers. Looking for
exact round-number walls (55,000) finds THAT bug but misses any member who is merely 40% short.
This audits the whole population against ground truth instead.

Uses the gateway's batch endpoint (100 ids/call, 10 credits/user) -> ~984 calls for 98k
members, ~$9.8. Read-only: writes a report + a repair list, changes nothing.

    python -m scripts.audit_crawl_completeness            # full audit
    python -m scripts.audit_crawl_completeness --limit 2000
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

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    stream=sys.stdout)
log = logging.getLogger("audit")
logging.getLogger("httpx").setLevel(logging.WARNING)

# a member is "incomplete" if we hold less than this fraction of their real following list.
# 0.95 (not 1.0) because following counts drift between crawl time and now (unfollows, and
# X's own count includes protected/suspended accounts the API won't return).
COMPLETE_AT = 0.95
MIN_MISSING = 200          # ignore trivial gaps (churn noise), only report real shortfalls
CHUNK = 100                # gateway batch size
CONCURRENCY = 6

OUT = "data/exports/crawl_completeness.csv"
REPAIR = "data/exports/crawl_repair_list.csv"


async def main() -> None:
    limit = None
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])

    eng = create_async_engine(os.environ["DATABASE_URL"], connect_args={"command_timeout": 3600})
    async with eng.connect() as c:
        await c.execute(text("set work_mem='512MB'"))
        log.info("counting stored edges per member (one pass over the edge graph)...")
        rows = (await c.execute(text("""
            SELECT s.user_id, s.username, COALESCE(e.n, 0) AS stored
            FROM smart_set s
            LEFT JOIN (SELECT follower_id, COUNT(*) n FROM edges GROUP BY follower_id) e
                   ON e.follower_id = s.user_id
            ORDER BY COALESCE(e.n, 0) DESC"""))).all()
    await eng.dispose()
    if limit:
        rows = rows[:limit]
    log.info("members to audit: %s", f"{len(rows):,}")

    tw = TwitterAPIClient()
    sem = asyncio.Semaphore(CONCURRENCY)
    real: dict[str, dict] = {}

    async def batch(chunk):
        async with sem:
            ids = [r[0] for r in chunk]
            try:
                users = await tw.batch_info_by_ids(ids)
            except Exception as e:  # noqa: BLE001 — a dead chunk must not kill the audit
                log.warning("batch failed (%s) — skipping %s ids", type(e).__name__, len(ids))
                return
            for u in users:
                uid = str(u.get("id") or "")
                if uid:
                    real[uid] = {
                        "following": u.get("following"),
                        "username": u.get("userName"),
                        "protected": bool(u.get("protected")),
                    }

    chunks = [rows[i:i + CHUNK] for i in range(0, len(rows), CHUNK)]
    for i in range(0, len(chunks), 20):
        await asyncio.gather(*(batch(ch) for ch in chunks[i:i + 20]))
        done = min((i + 20) * CHUNK, len(rows))
        log.info("  fetched %s/%s profiles (spent $%.2f)", f"{done:,}", f"{len(rows):,}",
                 getattr(tw, "usd_spent", 0.0))

    os.makedirs("data/exports", exist_ok=True)
    incomplete = []
    audited = missing_profile = 0
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["user_id", "username", "stored_edges", "real_following", "pct", "missing"])
        for uid, uname, stored in rows:
            info = real.get(str(uid))
            if not info or info.get("following") is None:
                missing_profile += 1
                continue
            following = int(info["following"] or 0)
            audited += 1
            if following <= 0:
                continue
            pct = stored / following
            miss = following - stored
            w.writerow([uid, uname or info.get("username"), stored, following,
                        round(pct * 100, 1), miss])
            if pct < COMPLETE_AT and miss >= MIN_MISSING and not info.get("protected"):
                incomplete.append((uid, uname or info.get("username"), stored, following, miss, pct))

    incomplete.sort(key=lambda r: -r[4])          # biggest gaps first
    with open(REPAIR, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["user_id", "username", "stored_edges", "real_following", "missing", "pct"])
        for r in incomplete:
            w.writerow([r[0], r[1], r[2], r[3], r[4], round(r[5] * 100, 1)])

    total_missing = sum(r[4] for r in incomplete)
    log.info("")
    log.info("=" * 60)
    log.info("audited            : %s members", f"{audited:,}")
    log.info("no profile returned: %s (dead/suspended)", f"{missing_profile:,}")
    log.info("INCOMPLETE crawls  : %s members (<%.0f%% captured, >=%s missing)",
             f"{len(incomplete):,}", COMPLETE_AT * 100, MIN_MISSING)
    log.info("total missing edges: %s", f"{total_missing:,}")
    log.info("est. repair cost   : $%.2f (ids endpoint @ $0.0049/1k)",
             total_missing / 1000 * 0.0049)
    log.info("=" * 60)
    log.info("worst offenders:")
    for uid, uname, stored, following, miss, pct in incomplete[:15]:
        log.info("  @%-22s %9s / %-9s  (%4.1f%%)  missing %s",
                 uname or uid, f"{stored:,}", f"{following:,}", pct * 100, f"{miss:,}")
    log.info("")
    log.info("full report  -> %s", OUT)
    log.info("repair list  -> %s", REPAIR)


if __name__ == "__main__":
    asyncio.run(main())
