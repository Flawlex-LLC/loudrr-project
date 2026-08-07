"""Test isolation for the mindshare pipeline.

DB-backed tests run against a throwaway temp SQLite — NEVER the real ``data/harvest.db``. We set
``DATABASE_URL`` to the temp file BEFORE any app import so the app engine binds to it. The Kaito
parity test reads ``harvest.db`` separately and read-only (raw sqlite3), so it can't mutate anything.
"""
import os
import pathlib

# MUST run before any `app.*` import so settings/engine bind to the temp DB.
_TMP = pathlib.Path(__file__).parent / "_tmp_mindshare.db"
for p in (_TMP, _TMP.with_suffix(".db-wal"), _TMP.with_suffix(".db-shm")):
    try:
        p.unlink()
    except FileNotFoundError:
        pass
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP.as_posix()}"

import pytest_asyncio  # noqa: E402


@pytest_asyncio.fixture
async def ms_db():
    """Fresh ms_* tables in the temp DB; yields the sessionmaker. Resets every test."""
    from sqlalchemy import delete

    from app.db.session import Base, SessionLocal, engine
    from app.mindshare import models as m

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with SessionLocal() as s:
        for T in (m.MsMover, m.MsSnapshot, m.MsBucket, m.MsContribution,
                  m.MsTweetRaw, m.MsIngestCursor, m.MsRoster):
            await s.execute(delete(T))
        await s.commit()
    yield SessionLocal
