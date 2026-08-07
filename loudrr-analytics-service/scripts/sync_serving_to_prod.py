"""One-shot sync of the SERVING tables from the local dev DB to prod Postgres.

    python -m scripts.sync_serving_to_prod            # uses LOUDRR_PG_PASSWORD from .env
    python -m scripts.sync_serving_to_prod --dry-run  # counts only

Pushes (batched — the public proxy resets large transfers; see scripts/backup_db.py):
  ranked_accounts, twitterscore_accounts, twitterscore_follows  (public profile/leaderboard)
  eng_tweet_raw, eng_edge, eng_cursor, eng_call, eng_token, eng_wallet  (engagement + KOL)

Idempotent: per-table upsert on the primary key (ON CONFLICT DO UPDATE), so re-running after
more local backfill just tops prod up. Prod-side tables are created via metadata if missing.
Requires the Coolify Postgres public port (5433) to be OPEN.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from sqlalchemy import func, inspect, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings  # noqa: F401 (loads .env)
from app.db.models import RankedAccount, TwitterScoreAccount, TwitterScoreFollow
from app.db.session import Base, SessionLocal as LocalSession
from app.engagement.models import (
    EngCall,
    EngCursor,
    EngEdge,
    EngToken,
    EngTweetRaw,
    EngWallet,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("sync_prod")

PROD_HOST, PROD_PORT, PROD_DB = "213.199.54.248", 5433, "loudrr_analytics"
BATCH = 500  # small batches survive the proxy

TABLES = [RankedAccount, TwitterScoreAccount, TwitterScoreFollow,
          EngTweetRaw, EngCursor, EngEdge, EngCall, EngToken, EngWallet]


def _prod_url() -> str:
    from dotenv import dotenv_values
    pw = dotenv_values(".env").get("LOUDRR_PG_PASSWORD")
    if not pw:
        sys.exit("LOUDRR_PG_PASSWORD missing from .env")
    return f"postgresql+asyncpg://postgres:{pw}@{PROD_HOST}:{PROD_PORT}/{PROD_DB}"


async def run(dry_run: bool) -> None:
    prod_engine = create_async_engine(_prod_url(), pool_size=2, max_overflow=2)
    ProdSession = async_sessionmaker(prod_engine, expire_on_commit=False)

    async with prod_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    for model in TABLES:
        table = model.__table__
        pk_cols = [c.name for c in table.primary_key.columns]
        async with LocalSession() as ls:
            total = (await ls.execute(select(func.count()).select_from(model))).scalar() or 0
        logger.info("%s: %s local rows", table.name, f"{total:,}")
        if dry_run or total == 0:
            continue

        moved = 0
        async with LocalSession() as ls, ProdSession() as ps:
            result = await ls.stream(select(model).execution_options(yield_per=BATCH))
            async for chunk in result.scalars().partitions(BATCH):
                payload = [
                    {c.name: getattr(row, c.name) for c in table.columns}
                    for row in chunk
                ]
                stmt = pg_insert(table).values(payload)
                stmt = stmt.on_conflict_do_update(
                    index_elements=pk_cols,
                    set_={c.name: stmt.excluded[c.name]
                          for c in table.columns if c.name not in pk_cols},
                )
                await ps.execute(stmt)
                await ps.commit()
                moved += len(payload)
                if moved % 5000 < BATCH:
                    logger.info("  %s: %s/%s", table.name, f"{moved:,}", f"{total:,}")
        logger.info("  %s: DONE %s rows", table.name, f"{moved:,}")

    await prod_engine.dispose()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    asyncio.run(run(args.dry_run))
