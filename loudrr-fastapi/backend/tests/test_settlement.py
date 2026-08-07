"""Sponsored-XP settlement tests (Django parity).

Pinned facts from the audit:
  - Sponsored != free. The creator's escrow IS still debited the karma
    (settlement.py:101 `post.escrow -= karma`). The XP is a separate, addit-
    ional, platform-funded top-up to the ENGAGER.
  - XP is its own currency, NON-spendable, stored on (User.sponsored_xp,
    User.total_sponsored_xp_earned, User.sponsored_engagements) and logged
    in `xp_transactions`.
  - The reader is `SPONSORED_XP_PER_ENGAGEMENT` (default 5). Setting it to 0
    must skip the XP write cleanly (no zero-amount ledger row).
"""
from decimal import Decimal

from app.core.time_utils import utcnow
from app.integrations import twitter
from app.models.engagement import Engagement
from app.models.post import Post
from app.models.site_setting import SiteSetting
from app.models.user import User
from app.models.verification_batch import VerificationBatch
from app.models.xp_transaction import XPTransaction, XPTransactionType
from app.services import claims, site_settings
from app.services.xp import XPService
from sqlalchemy import select


# ---- helpers (mirror test_claims.py, kept tiny on purpose) ----------
class _PassTwitter:
    async def verify_reply(self, *args, **kwargs):
        return {"passed": True, "reply_verified": True, "like_verified": True,
                "error": None, "skipped": False}


def _mock_pass(monkeypatch):
    monkeypatch.setattr(twitter, "get_twitter_client", lambda: _PassTwitter())


async def _make_post(db, *, owner_id, escrow="50", is_sponsored=False,
                     tweet_id="123", x_link="https://x.com/o/status/123"):
    p = Post(
        user_id=owner_id, x_link=x_link, tweet_id=tweet_id,
        escrow=Decimal(escrow), initial_escrow=Decimal("50"),
        status="active", platform="web", is_sponsored=is_sponsored,
    )
    db.add(p)
    await db.commit()
    return p


async def _make_engagement(db, *, user_id, post_id):
    e = Engagement(
        user_id=user_id, post_id=post_id, verified=False, credit_granted=False,
        clicked_at=utcnow(),
    )
    db.add(e)
    await db.commit()
    return e


async def _make_batch(db, *, user_id, engagement_ids):
    b = VerificationBatch(
        user_id=user_id, engagement_ids=[str(i) for i in engagement_ids],
        status="pending",
    )
    db.add(b)
    await db.commit()
    return b


def _seed_xp(db, value: int = 5):
    db.add(SiteSetting(
        key="SPONSORED_XP_PER_ENGAGEMENT",
        value=str(value),
        data_type="int",
    ))


# ============ XPService unit tests ============
async def test_xp_service_earn_bumps_all_three_counters(make_user, db_session):
    user = await make_user(telegram_id=9001, x_username="u")
    await XPService(db_session, user).earn_from_sponsored(
        amount=5, post_id=user.id, description="t",
    )
    await db_session.commit()
    assert user.sponsored_xp == 5
    assert user.total_sponsored_xp_earned == 5
    assert user.sponsored_engagements == 1
    # ledger row exists with the right shape
    rows = (await db_session.execute(
        select(XPTransaction).where(XPTransaction.user_id == user.id)
    )).scalars().all()
    assert len(rows) == 1
    assert rows[0].type == XPTransactionType.EARNED
    assert rows[0].amount == Decimal("5")
    assert rows[0].balance_after == Decimal("5")


async def test_xp_service_earn_skips_zero_amount(make_user, db_session):
    """amount=0 must NOT write a zero-amount ledger row — caller passes the
    raw site-setting and we silently no-op so an admin can disable XP without
    polluting the audit table."""
    user = await make_user(telegram_id=9002, x_username="u")
    result = await XPService(db_session, user).earn_from_sponsored(
        amount=0, post_id=user.id,
    )
    assert result is None
    assert user.sponsored_xp == 0
    assert user.sponsored_engagements == 0


async def test_xp_admin_grant_and_revoke(make_user, db_session):
    user = await make_user(telegram_id=9003, x_username="u")
    admin = await make_user(telegram_id=9004, role="admin")
    svc = XPService(db_session, user)

    await svc.admin_grant(amount=10, admin_user=admin)
    await db_session.commit()
    assert user.sponsored_xp == 10
    assert user.total_sponsored_xp_earned == 10

    await svc.admin_revoke(amount=3, admin_user=admin)
    await db_session.commit()
    assert user.sponsored_xp == 7
    # lifetime total is NOT decremented on revoke (matches Django)
    assert user.total_sponsored_xp_earned == 10


async def test_xp_revoke_clamps_to_available_balance(make_user, db_session):
    """Revoking more than available must clamp to the balance — the DB
    `sponsored_xp >= 0` constraint is the backstop, never reached."""
    user = await make_user(telegram_id=9005, x_username="u")
    admin = await make_user(telegram_id=9006, role="admin")
    svc = XPService(db_session, user)
    await svc.admin_grant(amount=2, admin_user=admin)
    await db_session.commit()

    await svc.admin_revoke(amount=100, admin_user=admin)
    await db_session.commit()
    assert user.sponsored_xp == 0  # clamped, not negative


async def test_xp_revoke_zero_balance_is_no_op(make_user, db_session):
    user = await make_user(telegram_id=9007, x_username="u")
    admin = await make_user(telegram_id=9008, role="admin")
    result = await XPService(db_session, user).admin_revoke(
        amount=5, admin_user=admin,
    )
    assert result is None
    assert user.sponsored_xp == 0


# ============ settlement integration ============
async def test_sponsored_engagement_awards_xp_AND_debits_escrow(
    client, make_user, db_session, monkeypatch,
):
    """The core Django-parity invariant: settling a passed engagement on a
    sponsored post awards the engager BOTH karma (deducted from escrow) AND
    XP (platform-funded, on top). Sponsored is NOT a free post."""
    _seed_xp(db_session, 5)
    await db_session.commit()
    site_settings._cache.clear()

    owner = await make_user(telegram_id=9101)
    viewer = await make_user(telegram_id=9102, x_username="viewer")
    post = await _make_post(
        db_session, owner_id=owner.id, escrow="50", is_sponsored=True,
    )
    eng = await _make_engagement(db_session, user_id=viewer.id, post_id=post.id)
    batch = await _make_batch(
        db_session, user_id=viewer.id, engagement_ids=[eng.id],
    )
    _mock_pass(monkeypatch)

    await claims.run_batch(db_session, batch.id)

    # karma flow: escrow STILL debited, viewer's karma STILL credited
    assert post.escrow == Decimal("49.0000")
    assert viewer.credits == Decimal("1.0000")
    # XP flow: the additional platform-funded top-up
    assert viewer.sponsored_xp == 5
    assert viewer.total_sponsored_xp_earned == 5
    assert viewer.sponsored_engagements == 1
    # ledger row written
    rows = (await db_session.execute(
        select(XPTransaction).where(XPTransaction.user_id == viewer.id)
    )).scalars().all()
    assert len(rows) == 1
    assert rows[0].type == XPTransactionType.EARNED
    assert rows[0].reference_id == post.id
    assert rows[0].reference_type == "post"


async def test_non_sponsored_engagement_awards_no_xp(
    client, make_user, db_session, monkeypatch,
):
    """Non-sponsored = baseline. Karma flow unchanged, no XP touched, no row
    in xp_transactions — proves the new code path is gated on is_sponsored."""
    _seed_xp(db_session, 5)
    await db_session.commit()
    site_settings._cache.clear()

    owner = await make_user(telegram_id=9201)
    viewer = await make_user(telegram_id=9202, x_username="viewer")
    post = await _make_post(
        db_session, owner_id=owner.id, escrow="50", is_sponsored=False,
    )
    eng = await _make_engagement(db_session, user_id=viewer.id, post_id=post.id)
    batch = await _make_batch(
        db_session, user_id=viewer.id, engagement_ids=[eng.id],
    )
    _mock_pass(monkeypatch)

    await claims.run_batch(db_session, batch.id)

    assert viewer.credits == Decimal("1.0000")
    assert post.escrow == Decimal("49.0000")
    assert viewer.sponsored_xp == 0
    assert viewer.total_sponsored_xp_earned == 0
    assert viewer.sponsored_engagements == 0
    rows = (await db_session.execute(
        select(XPTransaction).where(XPTransaction.user_id == viewer.id)
    )).scalars().all()
    assert rows == []


async def test_sponsored_xp_zero_setting_skips_xp_write(
    client, make_user, db_session, monkeypatch,
):
    """An admin can disable XP without unwiring the feature by setting the
    value to 0 — the engager still gets karma, the XP path no-ops."""
    _seed_xp(db_session, 0)
    await db_session.commit()
    site_settings._cache.clear()

    owner = await make_user(telegram_id=9301)
    viewer = await make_user(telegram_id=9302, x_username="viewer")
    post = await _make_post(
        db_session, owner_id=owner.id, escrow="50", is_sponsored=True,
    )
    eng = await _make_engagement(db_session, user_id=viewer.id, post_id=post.id)
    batch = await _make_batch(
        db_session, user_id=viewer.id, engagement_ids=[eng.id],
    )
    _mock_pass(monkeypatch)

    await claims.run_batch(db_session, batch.id)

    # karma still flowed
    assert viewer.credits == Decimal("1.0000")
    # but NO XP and NO ledger row (sponsored_xp=0 means feature-disabled)
    assert viewer.sponsored_xp == 0
    rows = (await db_session.execute(
        select(XPTransaction).where(XPTransaction.user_id == viewer.id)
    )).scalars().all()
    assert rows == []


async def test_sponsored_xp_default_when_setting_missing(
    client, make_user, db_session, monkeypatch,
):
    """No SPONSORED_XP_PER_ENGAGEMENT row in site_settings → service-level
    default 5 (matches the Django ECHO_CONFIG default at echo/settings.py:534)."""
    # deliberately DO NOT seed the setting
    site_settings._cache.clear()

    owner = await make_user(telegram_id=9401)
    viewer = await make_user(telegram_id=9402, x_username="viewer")
    post = await _make_post(
        db_session, owner_id=owner.id, escrow="50", is_sponsored=True,
    )
    eng = await _make_engagement(db_session, user_id=viewer.id, post_id=post.id)
    batch = await _make_batch(
        db_session, user_id=viewer.id, engagement_ids=[eng.id],
    )
    _mock_pass(monkeypatch)

    await claims.run_batch(db_session, batch.id)

    assert viewer.sponsored_xp == 5  # the default kicked in


async def test_xp_check_constraint_blocks_negative_balance(make_user, db_session):
    """Direct UPDATE bypassing XPService must still fail at the DB level —
    proves the `sponsored_xp_non_negative` check constraint is live. Run in
    a SAVEPOINT so the parent transaction (and the conftest teardown) stays
    usable after the constraint violation aborts the inner block."""
    from sqlalchemy import update
    from sqlalchemy.exc import IntegrityError

    user = await make_user(telegram_id=9501, x_username="u")
    raised = False
    try:
        async with db_session.begin_nested():
            await db_session.execute(
                update(User).where(User.id == user.id).values(sponsored_xp=-1)
            )
    except IntegrityError:
        raised = True
    assert raised, "expected IntegrityError from sponsored_xp_non_negative"
