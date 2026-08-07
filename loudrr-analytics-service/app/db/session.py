"""Async SQLAlchemy engine/session + declarative Base.

Storage is configurable via DATABASE_URL and **dialect-portable**:
  * SQLite (default for the one-shot harvest): `sqlite+aiosqlite:///./data/harvest.db`
    — in-process, no server, no Docker/WSL proxy, so the ConnectionRefused/crash class
    that took down the harvest is structurally impossible. WAL + synchronous=NORMAL keep
    commits cheap (no full fsync per txn).
  * Postgres (for the large graph / serving, if desired): same code, small pool.

The harvest writes are also BATCHED (see calibration._upsert_harvested) so commits — the
real disk-IO cost — happen a few hundred times per run, not ~5/sec.
"""
from collections.abc import AsyncGenerator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings

_is_sqlite = settings.database_url.startswith("sqlite")

if _is_sqlite:
    engine = create_async_engine(settings.database_url)

    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _rec):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")      # readers don't block the writer
        cur.execute("PRAGMA synchronous=NORMAL")    # no full-fsync per commit (safe w/ WAL)
        cur.execute("PRAGMA busy_timeout=60000")  # wait up to 60s for a write lock
        # (two harvesters can write concurrently; SQLite is single-writer, so queue not error)
        cur.close()
else:
    engine = create_async_engine(
        settings.database_url,
        pool_pre_ping=False,   # the per-checkout SELECT 1 is pure tax for our workload
        pool_size=2,           # single serial async writer needs ~1 connection
        max_overflow=2,
        pool_recycle=1800,
    )

SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


class Base(DeclarativeBase):
    pass


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session
