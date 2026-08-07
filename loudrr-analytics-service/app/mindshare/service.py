"""Orchestrator — one hourly tick of the mindshare pipeline.

    python -m app.mindshare.service --vertical crypto                 # full hourly run
    python -m app.mindshare.service --vertical crypto --max-accounts 50   # smaller test run

Runs the stages in order: ingest (incremental) -> score -> rebuild buckets -> snapshot -> movers.
Each stage is idempotent, so a failed/partial run is safe to retry. Intended to be invoked by a
plain hourly cron (RUN_MODE=mindshare on the server) — no browser, no Cloudflare.
"""
from __future__ import annotations

import argparse
import asyncio
import logging

from app.db.session import Base, engine
from app.mindshare.aggregate import compute_movers, rebuild_buckets, snapshot
from app.mindshare.ingest import ingest_vertical
from app.mindshare.score import score_vertical

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("ms.service")


async def run_pipeline(vertical: str = "crypto", *, max_pages: int = 3,
                       max_accounts: int | None = None, concurrency: int = 6) -> dict:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    ing = await ingest_vertical(vertical, max_pages=max_pages, max_accounts=max_accounts,
                                concurrency=concurrency)
    logger.info("ingest: %s", ing)
    sco = await score_vertical(vertical)
    logger.info("score: %s", sco)
    nb = await rebuild_buckets(vertical)
    snap = await snapshot(vertical)
    mv = await compute_movers(vertical)
    logger.info("aggregate: buckets=%s snapshot=%s movers=%s", nb, snap, mv)
    return {"ingest": ing, "score": sco, "buckets": nb, "snapshot": snap, "movers": mv}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vertical", default="crypto")
    ap.add_argument("--max-pages", type=int, default=3)
    ap.add_argument("--max-accounts", type=int, default=None)
    ap.add_argument("--concurrency", type=int, default=6)
    args = ap.parse_args()
    stats = await run_pipeline(args.vertical, max_pages=args.max_pages,
                               max_accounts=args.max_accounts, concurrency=args.concurrency)
    print(stats)


if __name__ == "__main__":
    asyncio.run(main())
