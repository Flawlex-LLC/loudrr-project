"""Create all tables (dev convenience; swap for Alembic before prod).

    python -m scripts.init_db
"""
import asyncio

from app.db.models import Base  # noqa: F401 (registers models on Base.metadata)
from app.db.session import engine


async def main() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("tables created")


if __name__ == "__main__":
    asyncio.run(main())
