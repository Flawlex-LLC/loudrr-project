"""Long-running mindshare worker — the hourly tick (RUN_MODE=mindshare on Coolify).

Loop: every ~hour, run the pipeline (ingest → score → buckets → snapshot → movers) for each
configured vertical. Every ``MINDSHARE_ROSTER_REFRESH_TICKS`` ticks (default 24 ≈ daily) it also
rebuilds the roster from the latest scraped Kaito data and recomputes our PageRank weights.

The Kaito scrape itself (the roster *source*) is a SEPARATE daily job so this worker stays focused
and resilient:  ``python -m scripts.scrape_kaito --vertical all``  (see docs/deploy_mindshare.md).
Every stage is idempotent, so a crashed/restarted worker simply resumes — nothing double-counts.

Env:
  MINDSHARE_VERTICALS=crypto[,ai,trading]   MINDSHARE_TICK_SECONDS=3600
  MINDSHARE_MAX_PAGES=2                       MINDSHARE_ROSTER_REFRESH_TICKS=24
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone

from app.mindshare.roster import rebuild_roster
from app.mindshare.service import run_pipeline
from app.mindshare.weights import compute_weights

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("ms.worker")

# `or "default"` (not the get() default): a present-but-EMPTY env row (common in the
# Coolify UI) must fall back too — int("") would crash-loop the container at import.
VERTICALS = [v.strip() for v in (os.environ.get("MINDSHARE_VERTICALS") or "crypto").split(",") if v.strip()]
TICK_SECONDS = int(os.environ.get("MINDSHARE_TICK_SECONDS") or "3600")
MAX_PAGES = int(os.environ.get("MINDSHARE_MAX_PAGES") or "2")
ROSTER_REFRESH_TICKS = int(os.environ.get("MINDSHARE_ROSTER_REFRESH_TICKS") or "24")


async def _refresh_roster() -> None:
    for v in VERTICALS:
        try:
            r = await rebuild_roster(v)
            w = await compute_weights(v)
            logger.info("roster/weights %s: roster=%s weights=%s", v, r, w)
        except Exception as e:  # noqa: BLE001 - no kaito data yet / transient; retry next refresh
            logger.warning("roster/weights %s skipped: %s", v, e)


async def worker() -> None:
    logger.info("mindshare worker up: verticals=%s tick=%ss max_pages=%s", VERTICALS, TICK_SECONDS, MAX_PAGES)
    tick = 0
    while True:
        start = datetime.now(timezone.utc)
        if tick % ROSTER_REFRESH_TICKS == 0:
            await _refresh_roster()
        for v in VERTICALS:
            try:
                stats = await run_pipeline(v, max_pages=MAX_PAGES)
                logger.info("tick=%s %s done: %s", tick, v, stats)
            except Exception:  # noqa: BLE001 - one vertical/tick failing must not kill the loop
                logger.exception("tick=%s %s pipeline failed", tick, v)
        tick += 1
        elapsed = (datetime.now(timezone.utc) - start).total_seconds()
        await asyncio.sleep(max(60.0, TICK_SECONDS - elapsed))


if __name__ == "__main__":
    asyncio.run(worker())
