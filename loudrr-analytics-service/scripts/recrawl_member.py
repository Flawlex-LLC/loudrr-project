"""Re-crawl ONE member's full following list (repairs a truncated crawl).

Why this exists: @JohnCena's crawl stopped at exactly 55,000 of his 1,058,482 following
(5.2%) — a truncation from an old `crawl_max_following_per_member` cap. He was the ONLY
member across 289M edges sitting at a round-number wall, so a targeted repair beats a full
re-crawl. The cap is now None (uncapped), and edge upserts are ON CONFLICT DO NOTHING, so
re-running is idempotent — the 55k existing edges simply dedupe.

    python -m scripts.recrawl_member JohnCena
    python -m scripts.recrawl_member JohnCena --dry-run
"""
from __future__ import annotations

import asyncio
import logging
import sys

sys.path.insert(0, ".")

from sqlalchemy import func, select  # noqa: E402

from app.clients.twitterapi import TwitterAPIClient  # noqa: E402
from app.db.models import Edge, SmartSetMember  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
# the crawl's own edge writer: chunks, ON CONFLICT DO NOTHING, write-lock + retry
from app.services.crawl import _flush_edges, _mark_crawled  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    stream=sys.stdout)
log = logging.getLogger("recrawl")

BATCH = 5000


async def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry = "--dry-run" in sys.argv
    if not args:
        raise SystemExit("usage: python -m scripts.recrawl_member <username> [--dry-run]")
    handle = args[0].lstrip("@")

    async with SessionLocal() as s:
        m = (await s.execute(
            select(SmartSetMember).where(func.lower(SmartSetMember.username) == handle.lower())
        )).scalars().first()
        if m is None:
            raise SystemExit(f"@{handle} is not a smart_set member")
        before = (await s.execute(
            select(func.count()).select_from(Edge).where(Edge.follower_id == m.user_id)
        )).scalar() or 0

    tw = TwitterAPIClient()
    prof = await tw.get_user_info(handle)
    real = int((prof or {}).get("following") or 0)
    log.info("@%s (id=%s): have %s edges, X says he follows %s (%.1f%% captured)",
             handle, m.user_id, f"{before:,}", f"{real:,}",
             (before / real * 100) if real else 0.0)
    if dry:
        log.info("dry-run: would fetch the full list (uncapped) and upsert the gap")
        return

    seen = 0
    buf: list[str] = []
    async for fid in tw.iter_following_ids(m.user_id, max_items=None):
        buf.append(fid)
        seen += 1
        if len(buf) >= BATCH:
            await _flush_edges(m.user_id, buf)
            buf.clear()
            log.info("  fetched %s / ~%s  (spent $%.2f)", f"{seen:,}", f"{real:,}",
                     getattr(tw, "usd_spent", 0.0))
    if buf:
        await _flush_edges(m.user_id, buf)
    await _mark_crawled(m.user_id)

    async with SessionLocal() as s:
        after = (await s.execute(
            select(func.count()).select_from(Edge).where(Edge.follower_id == m.user_id)
        )).scalar() or 0
    log.info("DONE @%s: fetched=%s  edges %s -> %s (+%s)  spent=$%.2f",
             handle, f"{seen:,}", f"{before:,}", f"{after:,}", f"{after - before:,}",
             getattr(tw, "usd_spent", 0.0))


if __name__ == "__main__":
    asyncio.run(main())
