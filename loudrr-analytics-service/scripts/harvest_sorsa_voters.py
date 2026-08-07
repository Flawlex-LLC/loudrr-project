"""Get Sorsa's score for our VOTERS — the 64,412 smart-set members we know nothing about.

Why: we can then rank our own 98k voter set by Sorsa's judgment. Voters Sorsa scores highly
are almost certainly in Sorsa's smart set; voters Sorsa scores near-zero are the ones diluting
us. That turns "cut the voter set" from a guess into a sorted list — and gets us close to
reverse-engineering Sorsa's actual smart set.

These members have NO usernames (they were discovered as follow-graph IDs and never enriched),
so we query Sorsa by user_id — verified working (/score?user_id=44196397 -> 5166.154).

PRIORITY ORDER: highest PageRank first. If quota runs out we'll have scored the voters that
actually move our numbers, not a random slice.

    SORSA_KEY=... python -m scripts.harvest_sorsa_voters
"""
from __future__ import annotations

import asyncio
import csv
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
log = logging.getLogger("sorsa_voters")
logging.getLogger("httpx").setLevel(logging.WARNING)

KEY = os.environ.get("SORSA_KEY") or ""
CONC = int(os.environ.get("SORSA_CONCURRENCY") or "16")
QPS = int(os.environ.get("SORSA_QPS") or "16")
RESERVE = 200

DDL = """
CREATE TABLE IF NOT EXISTS sorsa_voter_scores (
    user_id     varchar(32) PRIMARY KEY,
    username    varchar(64),
    sorsa_score double precision,
    our_pagerank double precision,
    fetched_at  timestamp DEFAULT now()
)
"""

CSV_PATH = "data/exports/sorsa_voter_scores.csv"


def quota() -> dict:
    with httpx.Client(timeout=30) as c:
        r = c.get("https://api.sorsa.io/v3/key-usage-info", headers={"ApiKey": KEY})
        return r.json() if r.status_code == 200 else {}


async def main() -> None:
    if not KEY:
        raise SystemExit("set SORSA_KEY")

    eng = create_async_engine(os.environ["DATABASE_URL"],
                              connect_args={"command_timeout": 300},
                              pool_pre_ping=True, pool_recycle=300)
    async with eng.begin() as c:
        await c.execute(text(DDL))

    async with eng.connect() as c:
        # every smart-set member we DON'T already have a Sorsa score for
        rows = (await c.execute(text("""
            SELECT s.user_id, s.username, s.score
            FROM smart_set s
            LEFT JOIN sorsa_voter_scores v ON v.user_id = s.user_id
            LEFT JOIN sorsa_scores ss ON lower(ss.username) = lower(s.username)
            WHERE v.user_id IS NULL AND ss.username IS NULL
            ORDER BY s.score DESC NULLS LAST"""))).all()

    # STRATIFIED sampling, not "top-K by PageRank". The unknowns' PageRank is nearly flat
    # (top 10k = only 39% of their weight), so a top-K pass would spend the whole key and still
    # leave 43k voters uncharacterised. Sampling every band instead lets us learn the RULE
    # ("below PageRank X, N% score zero on Sorsa") and then apply it to all 64k for free.
    # Set SORSA_STRATIFY=0 to fall back to a straight top-K pass.
    if os.environ.get("SORSA_STRATIFY", "1") == "1" and len(rows) > 0:
        bands = int(os.environ.get("SORSA_BANDS") or "16")
        per_band = int(os.environ.get("SORSA_PER_BAND") or "750")
        import random
        random.seed(17)
        sample: list = []
        n = len(rows)
        for b in range(bands):
            lo, hi = b * n // bands, (b + 1) * n // bands
            chunk = rows[lo:hi]
            if chunk:
                sample += random.sample(chunk, min(per_band, len(chunk)))
        log.info("stratified sample: %s voters across %s PageRank bands (%s/band)",
                 f"{len(sample):,}", bands, per_band)
        todo = sample
    else:
        todo = rows

    q = quota()
    budget = max(0, int(q.get("remaining_requests", 0)) - RESERVE)
    log.info("quota: %s remaining (expires %s)", f"{q.get('remaining_requests', 0):,}",
             q.get("valid_until", "?")[:10])
    log.info("voters still unscored: %s | budget: %s", f"{len(todo):,}", f"{budget:,}")
    if len(todo) > budget:
        log.warning("NOT ENOUGH QUOTA for all of them — scoring the %s highest-PageRank voters "
                    "(the ones that actually move our scores). %s will remain unscored.",
                    f"{budget:,}", f"{len(todo) - budget:,}")
    todo = todo[:budget]
    if not todo:
        log.info("nothing to do")
        await eng.dispose()
        return

    client = SorsaClient(api_key=KEY, qps=QPS, max_connections=CONC)
    sem = asyncio.Semaphore(CONC)
    got: list[dict] = []
    stats = {"ok": 0, "zero": 0, "err": 0}
    stop = asyncio.Event()

    os.makedirs("data/exports", exist_ok=True)
    new = not os.path.exists(CSV_PATH)
    fh = open(CSV_PATH, "a", newline="", encoding="utf-8")
    w = csv.writer(fh)
    if new:
        w.writerow(["user_id", "username", "sorsa_score", "our_pagerank"])

    async def one(uid, uname, pr):
        if stop.is_set():
            return
        async with sem:
            try:
                s = await client.score(user_id=str(uid))
            except Exception as e:  # noqa: BLE001
                if "Quota" in type(e).__name__ or "Budget" in type(e).__name__:
                    log.warning("quota exhausted — stopping cleanly")
                    stop.set()
                    return
                stats["err"] += 1
                return
            val = float(s or 0.0)
            got.append({"u": str(uid), "n": uname, "s": val, "p": float(pr or 0.0)})
            if val > 0:
                stats["ok"] += 1
            else:
                stats["zero"] += 1

    async def flush():
        if not got:
            return
        rows = list(got)
        got.clear()
        for attempt in range(4):
            try:
                async with eng.begin() as c:
                    await c.execute(text("""
                        insert into sorsa_voter_scores (user_id,username,sorsa_score,our_pagerank)
                        values (:u,:n,:s,:p)
                        on conflict (user_id) do update set sorsa_score=excluded.sorsa_score,
                          fetched_at=now()"""), rows)
                break
            except Exception:  # noqa: BLE001 — proxy drops connections on long runs
                if attempt == 3:
                    log.warning("db write failed — rows preserved in CSV")
                    break
                await eng.dispose()
                await asyncio.sleep(2 ** attempt)
        w.writerows([[r["u"], r["n"], r["s"], r["p"]] for r in rows])
        fh.flush()

    CH = 600
    pending = None
    for i in range(0, len(todo), CH):
        if stop.is_set():
            break
        await asyncio.gather(*(one(u, n, p) for u, n, p in todo[i:i + CH]))
        if pending:
            await pending
        pending = asyncio.create_task(flush())
        log.info("  %s/%s  scored=%s zero=%s err=%s  spent=%s",
                 min(i + CH, len(todo)), len(todo), stats["ok"], stats["zero"],
                 stats["err"], client.requests_spent)
    if pending:
        await pending
    await flush()
    await client.aclose()
    fh.close()

    async with eng.connect() as c:
        tot = (await c.execute(text("select count(*) from sorsa_voter_scores"))).scalar()
    await eng.dispose()
    log.info("DONE: %s scored (%s had score>0, %s were 0) | table now holds %s | local=%s",
             stats["ok"] + stats["zero"], stats["ok"], stats["zero"], f"{tot:,}", CSV_PATH)


if __name__ == "__main__":
    asyncio.run(main())
