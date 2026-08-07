"""Engagement-package test isolation. Reuses the root conftest's temp-SQLite DATABASE_URL
(set before any app import); this fixture just guarantees fresh eng_* tables per test."""
import pytest_asyncio


@pytest_asyncio.fixture
async def eng_db():
    """Fresh eng_* + smart_set tables in the temp DB; yields the sessionmaker."""
    from sqlalchemy import delete

    from app.db.models import RankedAccount, SmartSetMember
    from app.db.session import Base, SessionLocal, engine
    from app.engagement import models as em

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with SessionLocal() as s:
        for T in (em.EngRide, em.EngWallet, em.EngCall, em.EngToken, em.EngEdge, em.EngTweetRaw,
                  em.EngCursor, em.EngBackfill):
            await s.execute(delete(T))
        await s.execute(delete(SmartSetMember))
        await s.execute(delete(RankedAccount))
        await s.commit()
    yield SessionLocal
