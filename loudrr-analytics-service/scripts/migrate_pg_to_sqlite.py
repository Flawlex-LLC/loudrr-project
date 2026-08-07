"""One-time migration: copy the harvested data out of the (heavy, Docker) Postgres
into a single local SQLite file, so we never need Docker for the harvest again.

Run while Docker-Postgres is up:
    python -m scripts.migrate_pg_to_sqlite

Uses explicit source/dest engines (NOT the .env one) so it works regardless of how
DATABASE_URL is currently set. After it succeeds, point DATABASE_URL at the SQLite file.
"""
import asyncio

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.models import Base, Edge, GroundTruth, HarvestedScore, ScoreSnapshot, SmartSetMember

PG = "postgresql+asyncpg://postgres:postgres@localhost:5432/loudrr_analytics"
SQLITE = "sqlite+aiosqlite:///./data/harvest.db"
TABLES = [SmartSetMember, HarvestedScore, GroundTruth, ScoreSnapshot, Edge]


async def main() -> None:
    src = create_async_engine(PG)
    dst = create_async_engine(SQLITE)
    async with dst.begin() as c:
        await c.run_sync(Base.metadata.create_all)

    for model in TABLES:
        table = model.__table__
        async with src.connect() as s:
            rows = [dict(r) for r in (await s.execute(select(table))).mappings().all()]
        if not rows:
            print(f"  {table.name}: 0 (skip)")
            continue
        async with dst.begin() as d:
            # chunk the executemany so a huge table doesn't build one giant statement
            for i in range(0, len(rows), 1000):
                await d.execute(insert(table), rows[i : i + 1000])
        print(f"  {table.name}: migrated {len(rows)}")

    await src.dispose()
    await dst.dispose()
    print("done -> data/harvest.db  (now set DATABASE_URL=sqlite+aiosqlite:///./data/harvest.db)")


if __name__ == "__main__":
    asyncio.run(main())
