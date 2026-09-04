"""The crawl's refresh cadence — which members are DUE for a re-crawl.

Before this existed the crawl selected only ``last_crawled_at IS NULL``, so every
member was crawled exactly once and the follow graph then froze forever. These
tests pin the monthly-refresh behavior and the switch that turns it off.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import settings
from app.db.models import SmartSetMember
from app.services import crawl as crawl_svc


def _member(uid: str, *, crawled_days_ago: int | None) -> SmartSetMember:
    at = (
        None if crawled_days_ago is None
        else datetime.now(timezone.utc) - timedelta(days=crawled_days_ago)
    )
    return SmartSetMember(user_id=uid, username=f"u{uid}", last_crawled_at=at)


@pytest.fixture
async def db():
    from sqlalchemy import delete

    from app.db.session import Base, SessionLocal, engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with SessionLocal() as s:
        await s.execute(delete(SmartSetMember))
        await s.commit()
    yield SessionLocal


async def _seed(SessionLocal, rows):
    async with SessionLocal() as s:
        s.add_all(rows)
        await s.commit()


async def test_stale_members_are_due_fresh_ones_are_not(db, monkeypatch):
    monkeypatch.setattr(settings, "crawl_refresh_days", 30)
    await _seed(db, [
        _member("never", crawled_days_ago=None),
        _member("ancient", crawled_days_ago=90),
        _member("stale", crawled_days_ago=31),
        _member("fresh", crawled_days_ago=5),
        _member("justnow", crawled_days_ago=0),
    ])

    cutoff = crawl_svc.stale_before()
    due = [uid for uid, _ in await crawl_svc._select_members(None, cutoff=cutoff)]

    assert set(due) == {"never", "ancient", "stale"}
    assert await crawl_svc.count_due(cutoff) == 3


async def test_due_members_come_back_stalest_first(db, monkeypatch):
    """A budget-capped pass must spend on the most out-of-date members first."""
    monkeypatch.setattr(settings, "crawl_refresh_days", 30)
    await _seed(db, [
        _member("stale", crawled_days_ago=31),
        _member("never", crawled_days_ago=None),
        _member("ancient", crawled_days_ago=90),
    ])

    due = [uid for uid, _ in await crawl_svc._select_members(None, cutoff=crawl_svc.stale_before())]

    assert due == ["never", "ancient", "stale"]


async def test_refresh_can_be_disabled(db, monkeypatch):
    """crawl_refresh_days=0 freezes the graph: only never-crawled members qualify,
    which is exactly how the crawl behaved before the setting existed."""
    monkeypatch.setattr(settings, "crawl_refresh_days", 0)
    await _seed(db, [
        _member("never", crawled_days_ago=None),
        _member("ancient", crawled_days_ago=365),
    ])

    cutoff = crawl_svc.stale_before()
    assert cutoff is None
    due = [uid for uid, _ in await crawl_svc._select_members(None, cutoff=cutoff)]
    assert due == ["never"]


async def test_limit_is_not_wasted_on_fresh_members(db, monkeypatch):
    """The due-filter runs in SQL, so fresh members never consume a --limit slot."""
    monkeypatch.setattr(settings, "crawl_refresh_days", 30)
    await _seed(db, [_member(f"fresh{i}", crawled_days_ago=1) for i in range(5)]
                    + [_member("stale", crawled_days_ago=60)])

    due = [uid for uid, _ in await crawl_svc._select_members(2, cutoff=crawl_svc.stale_before())]

    assert due == ["stale"]
