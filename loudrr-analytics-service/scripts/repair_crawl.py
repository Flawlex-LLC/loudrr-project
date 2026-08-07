"""Audit ALL smart-set members for truncated follow-graphs and repair them.

The crawler used to believe the gateway's `has_next_page`, which intermittently reports
"finished" mid-list with no error — so members were stored partial and marked complete
(@JohnCena: 55k of 1.06M; @FerreWeb3: 78%). The scattered stored-counts (10.9k / 22.3k /
120.8k) mean there's no round-number pattern to grep for: the only reliable test is to compare
every member's stored edges against their REAL following count.

Two phases, both resumable:
  1. AUDIT   — batch-fetch real following counts (gateway batch endpoint is flaky: it 503s at
               100 ids, so we chunk small and retry), diff against stored edges.
  2. REPAIR  — re-crawl every member below the completeness bar, using the FIXED iterator that
               re-checks a has_next_page=false against a live cursor.

Idempotent: edges are ON CONFLICT DO NOTHING, so re-running only adds what's missing.

    DATABASE_URL=<prod> python -m scripts.repair_crawl --audit-only
    DATABASE_URL=<prod> python -m scripts.repair_crawl
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
log = logging.getLogger("repair")
logging.getLogger("httpx").setLevel(logging.WARNING)

COMPLETE_AT = float(os.environ.get("COMPLETE_AT") or "0.90")
MIN_MISSING = int(os.environ.get("MIN_MISSING") or "100")
BATCH = int(os.environ.get("AUDIT_BATCH") or "25")   # gateway 503s on 50/100-id chunks
# The audit is bound by the gateway's flakiness (503 -> exponential backoff), not bandwidth,
# so throughput comes from having many requests in flight rather than bigger chunks.
BATCH_CONC = int(os.environ.get("AUDIT_CONC") or "32")
REPAIR_CONC = int(os.environ.get("REPAIR_CONC") or "4")

AUDIT_CSV = "data/exports/crawl_audit_full.csv"
REPAIR_CSV = "data/exports/crawl_repaired.csv"


async def audit(eng, tw) -> list[tuple]:
    # Materialize the per-member edge counts ONCE into a table. Streaming a 289M-row GROUP BY
    # back over the network is what killed the first run (socket timeout); doing the aggregate
    # server-side and reading a 98k-row summary is both faster and durable.
    async with eng.begin() as c:
        await c.execute(text("set work_mem='512MB'"))
        await c.execute(text("set enable_nestloop=off"))
        log.info("materializing per-member edge counts (one scan of the edge graph)...")
        await c.execute(text("DROP TABLE IF EXISTS crawl_counts"))
        await c.execute(text("""
            CREATE TABLE crawl_counts AS
            SELECT s.user_id, s.username, COALESCE(e.n, 0)::bigint AS stored
            FROM smart_set s
            LEFT JOIN (SELECT follower_id, COUNT(*) n FROM edges GROUP BY follower_id) e
                   ON e.follower_id = s.user_id"""), execution_options={"timeout": 7200})
        await c.execute(text("ALTER TABLE crawl_counts ADD PRIMARY KEY (user_id)"))
    # persist resolved following-counts so a restart doesn't re-buy them from the gateway
    async with eng.begin() as c:
        await c.execute(text("""
            CREATE TABLE IF NOT EXISTS crawl_real_following (
                user_id varchar(32) PRIMARY KEY,
                following bigint,
                fetched_at timestamp DEFAULT now())"""))
    async with eng.connect() as c:
        rows = (await c.execute(text(
            "SELECT user_id, username, stored FROM crawl_counts ORDER BY stored DESC"))).all()
        cached = dict((await c.execute(text(
            "SELECT user_id, following FROM crawl_real_following"))).all())
    log.info("members: %s (following-count already known for %s)",
             f"{len(rows):,}", f"{len(cached):,}")

    sem = asyncio.Semaphore(BATCH_CONC)
    real: dict[str, int] = {str(k): int(v) for k, v in cached.items() if v is not None}
    dead = 0
    pending: list[dict] = []

    async def persist():
        if not pending:
            return
        rows_ = list(pending)
        pending.clear()
        async with eng.begin() as c:
            await c.execute(text("""
                insert into crawl_real_following (user_id, following) values (:u,:f)
                on conflict (user_id) do update set following=excluded.following"""), rows_)

    async def fetch(chunk):
        nonlocal dead
        async with sem:
            ids = [str(r[0]) for r in chunk]
            for attempt in range(4):          # gateway 503s are frequent -> retry hard
                try:
                    users = await tw.batch_info_by_ids(ids)
                    for u in users:
                        uid = str(u.get("id") or "")
                        f = u.get("following")
                        if uid and f is not None:
                            real[uid] = int(f)
                            pending.append({"u": uid, "f": int(f)})
                    return
                except Exception:  # noqa: BLE001
                    await asyncio.sleep(2 ** attempt)
            dead += len(ids)

    todo_rows = [r for r in rows if str(r[0]) not in real]
    log.info("still to resolve: %s", f"{len(todo_rows):,}")
    chunks = [todo_rows[i:i + BATCH] for i in range(0, len(todo_rows), BATCH)]
    ROUND = BATCH_CONC * 4          # keep the pool saturated between progress logs
    for i in range(0, len(chunks), ROUND):
        await asyncio.gather(*(fetch(ch) for ch in chunks[i:i + ROUND]))
        await persist()                      # survive a restart
        done = min((i + ROUND) * BATCH, len(todo_rows))
        log.info("  audited %s/%s  (resolved %s, spent $%.2f)", f"{done:,}",
                 f"{len(todo_rows):,}", f"{len(real):,}", getattr(tw, "usd_spent", 0.0))
    await persist()

    os.makedirs("data/exports", exist_ok=True)
    broken = []
    with open(AUDIT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["user_id", "username", "stored", "real_following", "pct", "missing"])
        for uid, uname, stored in rows:
            r = real.get(str(uid))
            if not r or r <= 0:
                continue
            pct = stored / r
            miss = r - stored
            w.writerow([uid, uname, stored, r, round(pct * 100, 1), miss])
            if pct < COMPLETE_AT and miss >= MIN_MISSING:
                broken.append((str(uid), uname, stored, r, miss))
    broken.sort(key=lambda x: -x[4])
    log.info("")
    log.info("=" * 64)
    log.info("resolved profiles : %s (unreadable: %s)", f"{len(real):,}", f"{dead:,}")
    log.info("TRUNCATED members : %s  (< %.0f%% of real following)",
             f"{len(broken):,}", COMPLETE_AT * 100)
    log.info("missing edges     : %s", f"{sum(b[4] for b in broken):,}")
    log.info("repair cost est.  : $%.2f", sum(b[4] for b in broken) / 1000 * 0.0049)
    log.info("=" * 64)
    for uid, uname, stored, r, miss in broken[:12]:
        log.info("  @%-22s %9s / %-9s (%4.1f%%)  missing %s",
                 uname or uid, f"{stored:,}", f"{r:,}", stored / r * 100, f"{miss:,}")
    log.info("full audit -> %s", AUDIT_CSV)
    return broken


async def repair(eng, tw, broken: list[tuple]) -> None:
    sem = asyncio.Semaphore(REPAIR_CONC)
    fixed = {"n": 0, "edges": 0}

    fh = open(REPAIR_CSV, "a", newline="", encoding="utf-8")
    w = csv.writer(fh)

    async def one(uid, uname, stored, real, miss):
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
                fixed["n"] += 1
                fixed["edges"] += gained
                w.writerow([uid, uname, stored, now, real, gained])
                fh.flush()
                log.info("  @%-20s %s -> %s (+%s)  [%s/%s]", uname or uid, f"{stored:,}",
                         f"{now:,}", f"{gained:,}", fixed["n"], len(broken))
            except Exception as e:  # noqa: BLE001 — one bad member must not kill the repair
                log.warning("  @%s repair failed: %s", uname or uid, type(e).__name__)

    log.info("\nrepairing %s truncated members (concurrency %s)...", len(broken), REPAIR_CONC)
    for i in range(0, len(broken), 40):
        await asyncio.gather(*(one(*b) for b in broken[i:i + 40]))
    fh.close()
    log.info("\nREPAIR DONE: %s members fixed, +%s edges recovered, spent $%.2f",
             f"{fixed['n']:,}", f"{fixed['edges']:,}", getattr(tw, "usd_spent", 0.0))


async def main() -> None:
    eng = create_async_engine(os.environ["DATABASE_URL"],
                              connect_args={"command_timeout": 7200})
    tw = TwitterAPIClient()
    broken = await audit(eng, tw)
    if "--audit-only" in sys.argv:
        log.info("audit-only: not repairing")
    elif broken:
        await repair(eng, tw, broken)
    await eng.dispose()


if __name__ == "__main__":
    asyncio.run(main())
