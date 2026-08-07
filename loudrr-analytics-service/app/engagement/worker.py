"""Long-running engagement worker — the daily tick (RUN_MODE=engagement on Coolify).

Loop: every ~ENGAGEMENT_TICK_SECONDS (default daily — the heatmap is a per-day metric, so
polling faster only pays the per-call floor more often), poll the smart-set universe's
timelines and extract engagement edges. Every stage is idempotent, so a crashed/restarted
worker simply resumes — nothing double-counts.

Env:
  ENGAGEMENT_TICK_SECONDS=86400   ENGAGEMENT_MAX_PAGES=3   ENGAGEMENT_MAX_ACCOUNTS=
  (universe/pacing/budget knobs live in app.core.config: ENGAGEMENT_UNIVERSE,
   ENGAGEMENT_RATE_CALLS, ENGAGEMENT_DAILY_BUDGET_USD)
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone

from app.engagement.service import run_once

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("eng.worker")

# `or "default"` (not the get() default): a present-but-EMPTY env row (common in the
# Coolify UI) must fall back too — int("") would crash-loop the container at import.
TICK_SECONDS = int(os.environ.get("ENGAGEMENT_TICK_SECONDS") or "86400")
MAX_PAGES = int(os.environ.get("ENGAGEMENT_MAX_PAGES") or "0") or None
MAX_ACCOUNTS = int(os.environ.get("ENGAGEMENT_MAX_ACCOUNTS") or "0") or None


async def worker() -> None:
    logger.info("engagement worker up: tick=%ss max_pages=%s max_accounts=%s",
                TICK_SECONDS, MAX_PAGES, MAX_ACCOUNTS)
    while True:
        start = datetime.now(timezone.utc)
        try:
            stats = await run_once(max_pages=MAX_PAGES, max_accounts=MAX_ACCOUNTS)
            logger.info("tick done: %s", stats)
        except Exception:  # noqa: BLE001 - a failed tick must not kill the loop
            logger.exception("engagement tick failed")
        elapsed = (datetime.now(timezone.utc) - start).total_seconds()
        await asyncio.sleep(max(60.0, TICK_SECONDS - elapsed))


if __name__ == "__main__":
    asyncio.run(worker())
