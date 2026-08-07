"""DB-level corruption guards — the headline of the hardening pass.

These prove the money/state invariants are enforced by PostgreSQL itself: even
if buggy or racing application code tried to write a corrupt row, the database
refuses it. This is what makes the ledger un-corruptible, and it is *stronger*
than the Django reference — Django enforces `credits >= 0` but lacks the totals
floors, the daily-earned floor, and the non-zero-ledger guard added here.

Each case asserts a real ``IntegrityError`` from the running Postgres, then
rolls back so the same session stays usable for the next assertion.
"""
from contextlib import asynccontextmanager
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.engagement import Engagement
from app.models.post import Post
from app.models.transaction import Transaction, TransactionType
from app.models.waitlist_entry import WaitlistEntry
from app.models.x_verification_request import XVerificationRequest


@asynccontextmanager
async def violates(db):
    """Assert the wrapped write raises IntegrityError, then rollback so the
    session is clean for the next check."""
    with pytest.raises(IntegrityError):
        yield
        await db.flush()
    await db.rollback()


def _post(user_id, **over):
    # takes a captured id (not a User) so it never triggers a lazy-load on an
    # object expired by an earlier rollback
    base = dict(
        user_id=user_id,
        x_link="https://x.com/a/status/1",
        escrow=Decimal("0"),
        initial_escrow=Decimal("0"),
        platform="web",
    )
    base.update(over)
    return Post(**base)


async def test_user_credits_cannot_go_negative(db_session, make_user):
    user = await make_user(credits=Decimal("10"))
    async with violates(db_session):
        user.credits = Decimal("-0.0001")


async def test_user_totals_cannot_go_negative(db_session, make_user):
    user = await make_user(
        credits=Decimal("10"),
        total_credits_earned=Decimal("10"),
        total_credits_spent=Decimal("5"),
    )
    async with violates(db_session):
        user.total_credits_earned = Decimal("-1")
    async with violates(db_session):
        user.total_credits_spent = Decimal("-1")
    async with violates(db_session):
        user.daily_credits_earned = Decimal("-1")


async def test_user_earned_must_cover_spent(db_session, make_user):
    # spent=5, earned=10 — dropping earned below spent is an audit corruption
    user = await make_user(
        total_credits_earned=Decimal("10"), total_credits_spent=Decimal("5")
    )
    async with violates(db_session):
        user.total_credits_earned = Decimal("4")


async def test_user_cannot_be_banned_and_whitelisted(db_session, make_user):
    user = await make_user(is_whitelisted=True)
    async with violates(db_session):
        user.is_banned = True


async def test_honesty_score_bounded(db_session, make_user):
    user = await make_user()
    async with violates(db_session):
        user.honesty_score = 51
    async with violates(db_session):
        user.honesty_score = -1


async def test_transaction_amount_cannot_be_zero(db_session, make_user):
    user = await make_user()
    async with violates(db_session):
        db_session.add(
            Transaction(
                user_id=user.id,
                type=TransactionType.EARNED,
                amount=Decimal("0"),
                balance_after=Decimal("0"),
                idempotency_key="zero",
            )
        )


async def test_transaction_idempotency_is_unique_per_type(db_session, make_user):
    user = await make_user()
    uid = user.id  # capture before any rollback expires the object
    db_session.add(
        Transaction(
            user_id=uid,
            type=TransactionType.EARNED,
            amount=Decimal("5"),
            balance_after=Decimal("5"),
            idempotency_key="k1",
        )
    )
    await db_session.commit()

    # same (user, type, key) → refused (no double-credit on a retry storm)
    async with violates(db_session):
        db_session.add(
            Transaction(
                user_id=uid,
                type=TransactionType.EARNED,
                amount=Decimal("5"),
                balance_after=Decimal("10"),
                idempotency_key="k1",
            )
        )

    # a DIFFERENT type may reuse the key — separate idempotency space
    db_session.add(
        Transaction(
            user_id=uid,
            type=TransactionType.SPENT,
            amount=Decimal("-5"),
            balance_after=Decimal("0"),
            idempotency_key="k1",
        )
    )
    await db_session.commit()


async def test_post_escrow_constraints(db_session, make_user):
    user = await make_user()
    uid = user.id  # capture before any rollback expires the object
    async with violates(db_session):  # escrow < 0
        db_session.add(_post(uid, escrow=Decimal("-1"), initial_escrow=Decimal("100")))
    async with violates(db_session):  # escrow > initial (inflation bug)
        db_session.add(_post(uid, escrow=Decimal("100"), initial_escrow=Decimal("50")))
    async with violates(db_session):  # completed yet escrow remains
        db_session.add(
            _post(uid, escrow=Decimal("50"), initial_escrow=Decimal("50"), status="completed")
        )
    async with violates(db_session):  # cancelled yet escrow remains
        db_session.add(
            _post(uid, escrow=Decimal("50"), initial_escrow=Decimal("50"), status="cancelled")
        )
    async with violates(db_session):  # status outside the state machine
        db_session.add(_post(uid, status="weird"))


async def test_engagement_credit_requires_verification(db_session, make_user):
    user = await make_user()
    poster = await make_user()
    post = _post(poster.id, escrow=Decimal("10"), initial_escrow=Decimal("10"))
    db_session.add(post)
    await db_session.commit()
    uid, post_id = user.id, post.id  # capture before the rollback

    async with violates(db_session):
        db_session.add(
            Engagement(user_id=uid, post_id=post_id, verified=False, credit_granted=True)
        )


async def test_engagement_one_per_user_post(db_session, make_user):
    user = await make_user()
    poster = await make_user()
    post = _post(poster.id, escrow=Decimal("10"), initial_escrow=Decimal("10"))
    db_session.add(post)
    await db_session.commit()
    uid, post_id = user.id, post.id  # capture before the rollback

    db_session.add(Engagement(user_id=uid, post_id=post_id))
    await db_session.commit()
    async with violates(db_session):
        db_session.add(Engagement(user_id=uid, post_id=post_id))


async def test_waitlist_status_must_be_valid(db_session):
    async with violates(db_session):
        db_session.add(
            WaitlistEntry(
                telegram_id=999_001,
                x_username="someone",
                referral_code="WLCODE01",
                status="weird",
            )
        )


async def test_x_verification_status_must_be_valid(db_session, make_user):
    user = await make_user()
    async with violates(db_session):
        db_session.add(
            XVerificationRequest(
                user_id=user.id,
                submitted_x_username="a",
                claimed_x_username="b",
                claimed_x_user_id="123",
                status="bogus",
            )
        )
