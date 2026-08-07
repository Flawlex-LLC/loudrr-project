"""Harvest Sorsa's score for EVERY ranked account -> prod table `sorsa_scores`.

Purpose: measure exactly how much we agree/disagree with Sorsa across the whole board (not a
biased sample). Our earlier reference set came from Sorsa's own top-followers lists, which
over-represented accounts Sorsa rates highly and skewed every calibration fit. This is the
unbiased ground truth.

Resumable: already-fetched usernames are skipped, so a crash/quota-stop just resumes.
Quota-aware: reads /key-usage-info and stops before exhausting the key.

    python -m scripts.harvest_sorsa_all              # all ranked accounts
    python -m scripts.harvest_sorsa_all --limit 500  # smoke test
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys

sys.path.insert(0, ".")

import httpx  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from app.clients.sorsa import SorsaClient  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    stream=sys.stdout)
log = logging.getLogger("harvest_sorsa")
logging.getLogger("httpx").setLevel(logging.WARNING)

KEY = os.environ.get("SORSA_KEY") or ""
CONCURRENCY = int(os.environ.get("SORSA_CONCURRENCY") or "6")
QPS = int(os.environ.get("SORSA_QPS") or "8")
RESERVE = 2000          # leave this many calls unspent on the key

DDL = """
CREATE TABLE IF NOT EXISTS sorsa_scores (
    username     varchar(64) PRIMARY KEY,
    user_id      varchar(32),
    sorsa_score  double precision,
    our_score    integer,
    our_raw      double precision,
    fetched_at   timestamp DEFAULT now()
)
"""


def quota() -> dict:
    with httpx.Client(timeout=30) as c:
        r = c.get("https://api.sorsa.io/v3/key-usage-info", headers={"ApiKey": KEY})
        return r.json() if r.status_code == 200 else {}


async def main() -> None:
    if not KEY:
        raise SystemExit("set SORSA_KEY")
    limit = None
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])

    q = quota()
    budget = max(0, int(q.get("remaining_requests", 0)) - RESERVE)
    log.info("sorsa quota: %s remaining (valid until %s) -> budget %s",
             f"{q.get('remaining_requests', 0):,}", q.get("valid_until"), f"{budget:,}")
    if budget <= 0:
        raise SystemExit("no quota left")

    eng = create_async_engine(os.environ["DATABASE_URL"], connect_args={"command_timeout": 300})
    async with eng.begin() as c:
        await c.execute(text(DDL))
    async with eng.connect() as c:
        todo = (await c.execute(text("""
            select r.username, r.user_id, r.score, r.raw_score
            from ranked_accounts r
            left join sorsa_scores s on s.username = r.username
            where s.username is null
            order by r.rank"""))).all()
    log.info("accounts still to fetch: %s", f"{len(todo):,}")
    if limit:
        todo = todo[:limit]
    todo = todo[:budget]
    if not todo:
        log.info("nothing to do — already complete")
        await eng.dispose()
        return

    # pool must be >= concurrency or it silently throttles (default 5 => ~1.6 req/s)
    client = SorsaClient(api_key=KEY, qps=QPS, max_connections=CONCURRENCY)
    sem = asyncio.Semaphore(CONCURRENCY)
    got: list[tuple] = []
    stats = {"ok": 0, "miss": 0, "err": 0}
    stop = asyncio.Event()

    async def one(uname, uid, our, raw):
        if stop.is_set():
            return
        async with sem:
            try:
                s = await client.score(uname)
            except Exception as e:  # noqa: BLE001
                name = type(e).__name__
                if "Quota" in name or "Budget" in name:
                    log.warning("quota exhausted — stopping cleanly")
                    stop.set()
                    return
                stats["err"] += 1
                return
            if s and s > 0:
                got.append((uname, uid, float(s), our, raw))
                stats["ok"] += 1
            else:
                stats["miss"] += 1

    # local CSV mirror — appended as we go, so records survive an interrupt/quota stop
    import csv
    os.makedirs("data/exports", exist_ok=True)
    csv_path = "data/exports/sorsa_vs_loudrr.csv"
    new_file = not os.path.exists(csv_path)
    csv_fh = open(csv_path, "a", newline="", encoding="utf-8")
    csv_w = csv.writer(csv_fh)
    if new_file:
        csv_w.writerow(["username", "user_id", "sorsa_score", "our_score", "our_raw"])

    async def flush():
        if not got:
            return
        rows = list(got)
        got.clear()
        # ONE executemany, not N round-trips: per-row inserts over the public proxy were the
        # real bottleneck (harvest ran at ~2 req/s instead of the ~10/s the API allows).
        params = [{"u": r[0], "i": r[1], "s": r[2], "o": r[3], "r": r[4]} for r in rows]
        async with eng.begin() as c:
            await c.execute(text("""
                insert into sorsa_scores (username,user_id,sorsa_score,our_score,our_raw)
                values (:u,:i,:s,:o,:r)
                on conflict (username) do update set
                  sorsa_score=excluded.sorsa_score, fetched_at=now()"""), params)
        csv_w.writerows(rows)          # local mirror
        csv_fh.flush()

    CH = 600
    pending_flush: asyncio.Task | None = None
    for i in range(0, len(todo), CH):
        if stop.is_set():
            break
        await asyncio.gather(*(one(u, i_, o, r) for u, i_, o, r in todo[i:i + CH]))
        # let the previous DB write finish while we've already started the next fetch batch
        if pending_flush is not None:
            await pending_flush
        pending_flush = asyncio.create_task(flush())
        log.info("  %s/%s  ok=%s miss=%s err=%s  spent=%s",
                 min(i + CH, len(todo)), len(todo), stats["ok"], stats["miss"],
                 stats["err"], client.requests_spent)
    if pending_flush is not None:
        await pending_flush
    await flush()
    await client.aclose()
    csv_fh.close()

    async with eng.connect() as c:
        total = (await c.execute(text("select count(*) from sorsa_scores"))).scalar()
    await eng.dispose()
    log.info("DONE: %s ok, %s missing, %s errors | sorsa_scores(prod)=%s rows | local=%s",
             stats["ok"], stats["miss"], stats["err"], f"{total:,}", csv_path)


if __name__ == "__main__":
    asyncio.run(main())
