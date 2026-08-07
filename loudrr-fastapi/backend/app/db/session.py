from collections.abc import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from app.core.config import settings

# the engine, it is the db connection pool. Pool sizing + pre-ping matter at
# scale: pool_pre_ping replaces dropped/stale connections transparently (safe
# across Postgres restarts and PgBouncer), and the sizes are configurable so a
# deployment can stay under Postgres max_connections. See backend/tests/SCALING.md.
engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    pool_pre_ping=True,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_timeout=settings.db_pool_timeout,
    pool_recycle=settings.db_pool_recycle,
)

# the sessionmaker, it is a factory for creating new sessions.
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session
