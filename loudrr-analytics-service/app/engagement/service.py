"""Pipeline orchestration: ingest (bronze) -> edges + calls (silver) -> onchain enrich.
Each stage is idempotent, so a crashed run simply resumes on the next invocation — nothing
double-counts.

    python -m app.engagement.service --max-accounts 50 --max-pages 2 --budget 1.0   # pilot
    python -m app.engagement.service                                                 # full pass
"""
from __future__ import annotations

import argparse
import asyncio
import logging

from app.engagement.calls import extract_calls
from app.engagement.extract import extract_edges
from app.engagement.ingest import ingest_members
from app.engagement.onchain import enrich_calls, refresh_tokens

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("eng.service")


async def run_once(*, max_pages: int | None = None, max_accounts: int | None = None,
                   budget_usd: float | None = None, client=None, dex=None) -> dict:
    ing = await ingest_members(max_pages=max_pages, max_accounts=max_accounts,
                               budget_usd=budget_usd, client=client)
    ext = await extract_edges()
    calls = await extract_calls()
    # onchain enrichment is free (DexScreener) but still isolated: its failure only means
    # unresolved tokens wait for the next tick.
    try:
        enrich = await enrich_calls(dex=dex)
        refresh = await refresh_tokens(dex=dex)
    except Exception:  # noqa: BLE001 - never let the free-API layer kill the paid pipeline
        logger.exception("onchain enrichment failed; will retry next tick")
        enrich, refresh = {"resolved": 0}, {"refreshed": 0}
    return {"ingest": ing, "extract": ext, "calls": calls,
            "enrich": enrich, "refresh": refresh}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-pages", type=int, default=None)
    ap.add_argument("--max-accounts", type=int, default=None)
    ap.add_argument("--budget", type=float, default=None, help="USD ceiling for this run")
    args = ap.parse_args()
    stats = await run_once(max_pages=args.max_pages, max_accounts=args.max_accounts,
                           budget_usd=args.budget)
    logger.info("engagement run done: %s", stats)
    print(stats)


if __name__ == "__main__":
    asyncio.run(main())
