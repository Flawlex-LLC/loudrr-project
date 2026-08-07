"""Unit tests for CreditService — the money. Every path commits to the
loudrr_test DB so the FOR UPDATE locks and CHECK constraints are real."""
import uuid
from decimal import Decimal
from datetime import timedelta

import pytest

from app.core.time_utils import utcnow
from app.services.credits import (
    CreditService, InsufficientCreditsError, DailyCapReachedError,
)
from app.models.transaction import TransactionType


# ---- earn ----
async def test_earn_adds_credits(db_session, make_user):
    user = await make_user()
    txn = await CreditService(db_session, user).earn(Decimal("50"), idempotency_key="e1")
    await db_session.refresh(user)
    assert user.credits == Decimal("50")
    assert user.total_credits_earned == Decimal("50")
    assert user.daily_credits_earned == Decimal("50")
    assert txn.type == TransactionType.EARNED
    assert txn.amount == Decimal("50")
    assert txn.balance_after == Decimal("50")


async def test_earn_requires_idempotency_key(db_session, make_user):
    user = await make_user()
    with pytest.raises(ValueError):
        await CreditService(db_session, user).earn(Decimal("10"), idempotency_key="")


async def test_earn_is_idempotent(db_session, make_user):
    user = await make_user()
    svc = CreditService(db_session, user)
    first = await svc.earn(Decimal("50"), idempotency_key="same")
    second = await svc.earn(Decimal("50"), idempotency_key="same")
    assert first.id == second.id          # same row returned, not a new one
    await db_session.refresh(user)
    assert user.credits == Decimal("50")  # NOT doubled


async def test_earn_trims_to_daily_cap(db_session, make_user):
    # cap is 100 (seeded in conftest); already earned 90 today
    user = await make_user(
        daily_credits_earned=Decimal("90"),
        daily_earned_reset_at=utcnow(),
    )
    txn = await CreditService(db_session, user).earn(Decimal("20"), idempotency_key="cap")
    assert txn.amount == Decimal("10")    # trimmed to the 10 of headroom left
    await db_session.refresh(user)
    assert user.daily_credits_earned == Decimal("100")


async def test_earn_at_cap_raises(db_session, make_user):
    user = await make_user(
        daily_credits_earned=Decimal("100"),
        daily_earned_reset_at=utcnow(),
    )
    with pytest.raises(DailyCapReachedError):
        await CreditService(db_session, user).earn(Decimal("5"), idempotency_key="over")


async def test_earn_resets_on_new_day(db_session, make_user):
    user = await make_user(
        daily_credits_earned=Decimal("90"),
        daily_earned_reset_at=utcnow() - timedelta(days=1),
    )
    txn = await CreditService(db_session, user).earn(Decimal("20"), idempotency_key="newday")
    assert txn.amount == Decimal("20")    # full amount — yesterday's tally reset
    await db_session.refresh(user)
    assert user.daily_credits_earned == Decimal("20")


# ---- spend ----
async def test_spend_deducts(db_session, make_user):
    user = await make_user(credits=Decimal("100"), total_credits_earned=Decimal("100"))
    txn = await CreditService(db_session, user).spend(Decimal("30"), idempotency_key="s1")
    await db_session.refresh(user)
    assert user.credits == Decimal("70")
    assert user.total_credits_spent == Decimal("30")
    assert txn.amount == Decimal("-30")
    assert txn.balance_after == Decimal("70")


async def test_spend_insufficient(db_session, make_user):
    user = await make_user(credits=Decimal("10"), total_credits_earned=Decimal("10"))
    with pytest.raises(InsufficientCreditsError):
        await CreditService(db_session, user).spend(Decimal("50"), idempotency_key="s2")


async def test_spend_rejects_non_positive(db_session, make_user):
    user = await make_user(credits=Decimal("10"), total_credits_earned=Decimal("10"))
    with pytest.raises(ValueError):
        await CreditService(db_session, user).spend(Decimal("0"), idempotency_key="s3")


# ---- refund / admin_grant / penalty ----
async def test_refund_adds_back(db_session, make_user):
    user = await make_user(credits=Decimal("10"), total_credits_earned=Decimal("10"))
    txn = await CreditService(db_session, user).refund(Decimal("5"), idempotency_key="r1")
    await db_session.refresh(user)
    assert user.credits == Decimal("15")
    assert txn.amount == Decimal("5")
    assert txn.type == TransactionType.REFUND


async def test_admin_grant_bypasses_cap(db_session, make_user):
    user = await make_user(
        daily_credits_earned=Decimal("100"),
        daily_earned_reset_at=utcnow(),
    )
    txn = await CreditService(db_session, user).admin_grant(
        Decimal("1000"), admin_id=uuid.uuid4(), idempotency_key="g1"
    )
    await db_session.refresh(user)
    assert user.credits == Decimal("1000")   # daily cap ignored for admin grants
    assert txn.type == TransactionType.ADMIN_GRANT


async def test_apply_penalty_deducts(db_session, make_user):
    user = await make_user(credits=Decimal("100"), total_credits_earned=Decimal("100"))
    txn = await CreditService(db_session, user).apply_penalty(
        Decimal("30"), admin_id=uuid.uuid4(), idempotency_key="p1"
    )
    await db_session.refresh(user)
    assert user.credits == Decimal("70")
    assert txn.amount == Decimal("-30")
    assert txn.type == TransactionType.APPLY_PENALTY


# ---- hardening: a penalty can never corrupt the balance ----
async def test_penalty_larger_than_balance_floors_at_zero(db_session, make_user):
    """A penalty bigger than the balance takes only what's there — the balance
    floors at 0, never negative (graceful sad-path; the DB check is the backstop)."""
    user = await make_user(credits=Decimal("30"), total_credits_earned=Decimal("30"))
    txn = await CreditService(db_session, user).apply_penalty(
        Decimal("100"), admin_id=uuid.uuid4(), idempotency_key="p_over"
    )
    await db_session.refresh(user)
    assert user.credits == Decimal("0")          # floored, not -70
    assert txn.amount == Decimal("-30")          # only the available 30 was taken
    assert txn.balance_after == Decimal("0")


async def test_penalty_on_empty_balance_is_noop(db_session, make_user):
    """Nothing to take → no 0-amount ledger row is written (that would violate
    transaction_amount_nonzero); the call returns None and the balance stays 0."""
    user = await make_user(credits=Decimal("0"))
    txn = await CreditService(db_session, user).apply_penalty(
        Decimal("50"), admin_id=uuid.uuid4(), idempotency_key="p_empty"
    )
    assert txn is None
    await db_session.refresh(user)
    assert user.credits == Decimal("0")


async def test_penalty_is_idempotent(db_session, make_user):
    user = await make_user(credits=Decimal("100"), total_credits_earned=Decimal("100"))
    svc = CreditService(db_session, user)
    first = await svc.apply_penalty(Decimal("30"), admin_id=uuid.uuid4(), idempotency_key="dup")
    second = await svc.apply_penalty(Decimal("30"), admin_id=uuid.uuid4(), idempotency_key="dup")
    assert first.id == second.id                 # same row, not a second deduction
    await db_session.refresh(user)
    assert user.credits == Decimal("70")         # deducted exactly once


# ---- hardening: no operation may write a zero / negative amount ----
async def test_earn_rejects_non_positive(db_session, make_user):
    user = await make_user()
    with pytest.raises(ValueError):
        await CreditService(db_session, user).earn(Decimal("0"), idempotency_key="z")


async def test_refund_rejects_non_positive(db_session, make_user):
    user = await make_user()
    with pytest.raises(ValueError):
        await CreditService(db_session, user).refund(Decimal("0"), idempotency_key="z")


async def test_admin_grant_rejects_non_positive(db_session, make_user):
    user = await make_user()
    with pytest.raises(ValueError):
        await CreditService(db_session, user).admin_grant(
            Decimal("0"), admin_id=uuid.uuid4(), idempotency_key="z"
        )


async def test_penalty_rejects_non_positive(db_session, make_user):
    user = await make_user(credits=Decimal("10"), total_credits_earned=Decimal("10"))
    with pytest.raises(ValueError):
        await CreditService(db_session, user).apply_penalty(
            Decimal("0"), admin_id=uuid.uuid4(), idempotency_key="z"
        )