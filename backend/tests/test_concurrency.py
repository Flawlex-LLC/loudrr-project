"""Security: prove the SELECT ... FOR UPDATE row lock actually prevents a
double-spend under genuine concurrency (two parallel requests, real separate
DB connections). Without the lock, both could read the old balance and oversell.
"""
import asyncio
import uuid
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.errors import BadRequest
from app.models.site_setting import SiteSetting
from app.models.user import User
from app.models.waitlist_entry import WaitlistEntry
from app.models.x_profile import XProfile
from app.services import posts as posts_svc
from app.services import site_settings
from app.services import waitlist as waitlist_svc
from app.services.credits import CreditService, InsufficientCreditsError

TEST_DATABASE_URL = settings.database_url.rsplit("/", 1)[0] + "/loudrr_test"


class _FakeTwitter:
    async def get_tweet_content(self, tweet_id):
        return {
            "tweet_id": tweet_id, "text": "gm", "author_id": "X1",
            "author_username": "me", "author_name": "Me", "author_avatar": "",
            "media": [], "created_at": "",
        }


async def test_concurrent_spend_cannot_oversell(db_session, make_user):
    # db_session created the schema + this committed user on loudrr_test
    user = await make_user(credits=Decimal("100"), total_credits_earned=Decimal("100"))
    uid = user.id

    # two INDEPENDENT sessions/connections — real concurrency, not the fixture's one
    engine = create_async_engine(TEST_DATABASE_URL)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async def attempt() -> str:
        async with Session() as s:
            u = (await s.execute(select(User).where(User.id == uid))).scalar_one()
            try:
                await CreditService(s, u).spend(
                    Decimal("60"), idempotency_key=f"spend-{uuid.uuid4().hex}"
                )
                return "ok"
            except InsufficientCreditsError:
                return "insufficient"

    try:
        r1, r2 = await asyncio.gather(attempt(), attempt())
    finally:
        await engine.dispose()

    # exactly one of the two 60-spends on a 100 balance may succeed
    assert sorted([r1, r2]) == ["insufficient", "ok"]
    await db_session.refresh(user)
    assert user.credits == Decimal("40")   # never -20 → the lock held


async def test_concurrent_earn_and_spend_stay_consistent(db_session, make_user):
    """An earn and a spend racing on the same balance must serialize, never
    interleave into a wrong or negative total. 100 + 50 − 80 = 70, either order."""
    user = await make_user(credits=Decimal("100"), total_credits_earned=Decimal("100"))
    uid = user.id
    engine = create_async_engine(TEST_DATABASE_URL)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async def do_earn():
        async with Session() as s:
            u = (await s.execute(select(User).where(User.id == uid))).scalar_one()
            await CreditService(s, u).earn(Decimal("50"), idempotency_key="race-earn")

    async def do_spend():
        async with Session() as s:
            u = (await s.execute(select(User).where(User.id == uid))).scalar_one()
            await CreditService(s, u).spend(Decimal("80"), idempotency_key="race-spend")

    try:
        await asyncio.gather(do_earn(), do_spend())
    finally:
        await engine.dispose()

    await db_session.refresh(user)
    assert user.credits == Decimal("70")           # consistent regardless of order
    assert user.credits >= Decimal("0")            # never corrupted negative


async def test_concurrent_submit_cannot_overspend(db_session, make_user, monkeypatch):
    """Two posts submitted at once by a user who can only afford one: exactly
    one succeeds, the balance hits 0 (never negative), one post survives."""
    db_session.add(SiteSetting(key="POST_COST_MIN", value="10", data_type="int"))
    db_session.add(SiteSetting(key="POST_COST_MAX", value="200", data_type="int"))
    user = await make_user(
        x_username="me", credits=Decimal("10"), total_credits_earned=Decimal("10")
    )
    db_session.add(XProfile(user_id=user.id, x_user_id="X1", username="me", score=0))
    await db_session.commit()
    site_settings._cache.clear()
    uid = user.id

    monkeypatch.setattr(posts_svc, "get_twitter_client", lambda: _FakeTwitter())

    engine = create_async_engine(TEST_DATABASE_URL)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async def submit(n) -> str:
        async with Session() as s:
            u = (await s.execute(select(User).where(User.id == uid))).scalar_one()
            try:
                await posts_svc.submit_post(
                    s, user=u, x_link=f"https://x.com/me/status/{n}"
                )
                return "ok"
            except BadRequest:
                return "rejected"

    try:
        r1, r2 = await asyncio.gather(submit(111), submit(222))
    finally:
        await engine.dispose()

    assert sorted([r1, r2]) == ["ok", "rejected"]   # only one could afford it
    await db_session.refresh(user)
    assert user.credits == Decimal("0")             # spent down to zero, not negative
    from app.models.post import Post
    active = (
        await db_session.execute(
            select(func.count()).select_from(Post).where(Post.status == "active")
        )
    ).scalar_one()
    assert active == 1                              # the rejected submit rolled its post back


async def test_concurrent_waitlist_register_makes_one_entry(db_session, make_user):
    """The same telegram_id registering twice at once yields exactly one row —
    the unique constraint + the race re-query guarantee it (no duplicates)."""
    tg = {"id": 770_001, "username": "u", "first_name": "U"}
    payload = SimpleNamespace(
        x_link="https://x.com/dupuser",
        region=None, niche=None, other_platforms=[], referral_code=None,
    )
    engine = create_async_engine(TEST_DATABASE_URL)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async def reg():
        async with Session() as s:
            return await waitlist_svc.register_entry(s, tg_user=tg, payload=payload)

    try:
        results = await asyncio.gather(reg(), reg(), return_exceptions=True)
    finally:
        await engine.dispose()

    # whatever the timing, the DB holds exactly one entry for this telegram_id
    count = (
        await db_session.execute(
            select(func.count()).select_from(WaitlistEntry)
            .where(WaitlistEntry.telegram_id == 770_001)
        )
    ).scalar_one()
    assert count == 1
    # and at least one caller observed success (didn't both blow up)
    assert any(not isinstance(r, Exception) for r in results)
