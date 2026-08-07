"""Unit tests for the streak service (Django parity — apply_streak_for_settlement
mirrors core/services/engagements.py:_update_streak using utcnow().date()).

These hit the real loudrr_test DB so the streak invariants
(current_streak_non_negative, longest_streak_ge_current) are exercised end-to-end.
"""
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select

from app.core.time_utils import utcnow
from app.models.site_setting import SiteSetting
from app.models.transaction import Transaction, TransactionType
from app.services import site_settings
from app.services import streaks


def _seed_bonus(db, threshold: int, value: int):
    db.add(SiteSetting(
        key=f"STREAK_{threshold}_DAY_BONUS",
        value=str(value), data_type="int",
    ))


def _seed_multiplier(db, threshold: int, value: str):
    db.add(SiteSetting(
        key=f"STREAK_{threshold}_DAY_MULTIPLIER",
        value=value, data_type="decimal",
    ))


# ============ apply_streak_for_settlement — increment rules ============
async def test_first_engagement_sets_streak_to_one(make_user, db_session):
    """No prior last_engagement_date — Django parity: streak starts at 1."""
    user = await make_user(telegram_id=30001)
    out = await streaks.apply_streak_for_settlement(db_session, user)
    await db_session.commit()
    assert out["incremented"] is True
    assert out["new_streak"] == 1
    assert out["crossed_threshold"] is None
    assert out["bonus_awarded"] == Decimal("0")
    await db_session.refresh(user)
    assert user.current_streak == 1
    assert user.longest_streak == 1
    assert user.last_engagement_date == utcnow().date()


async def test_same_day_is_noop(make_user, db_session):
    """Second engagement the same UTC day must NOT bump or re-pay any bonus."""
    today = utcnow().date()
    user = await make_user(
        telegram_id=30002, current_streak=3, longest_streak=3,
        last_engagement_date=today,
    )
    out = await streaks.apply_streak_for_settlement(db_session, user)
    await db_session.commit()
    assert out["incremented"] is False
    assert out["new_streak"] == 3
    assert out["crossed_threshold"] is None
    await db_session.refresh(user)
    assert user.current_streak == 3


async def test_consecutive_day_increments(make_user, db_session):
    """last_engagement_date == today-1 → streak += 1."""
    today = utcnow().date()
    user = await make_user(
        telegram_id=30003, current_streak=4, longest_streak=4,
        last_engagement_date=today - timedelta(days=1),
    )
    out = await streaks.apply_streak_for_settlement(db_session, user)
    await db_session.commit()
    assert out["incremented"] is True
    assert out["new_streak"] == 5
    await db_session.refresh(user)
    assert user.current_streak == 5
    assert user.longest_streak == 5  # bumped past previous max


async def test_gap_resets_to_one(make_user, db_session):
    """Missed a day — streak resets to 1, longest is preserved."""
    today = utcnow().date()
    user = await make_user(
        telegram_id=30004, current_streak=10, longest_streak=10,
        last_engagement_date=today - timedelta(days=3),
    )
    out = await streaks.apply_streak_for_settlement(db_session, user)
    await db_session.commit()
    assert out["new_streak"] == 1
    await db_session.refresh(user)
    assert user.current_streak == 1
    assert user.longest_streak == 10  # lifetime record kept


async def test_longest_only_updates_when_exceeded(make_user, db_session):
    """A consecutive bump that's still below longest must NOT lower longest."""
    today = utcnow().date()
    user = await make_user(
        telegram_id=30005, current_streak=3, longest_streak=20,
        last_engagement_date=today - timedelta(days=1),
    )
    await streaks.apply_streak_for_settlement(db_session, user)
    await db_session.commit()
    await db_session.refresh(user)
    assert user.current_streak == 4
    assert user.longest_streak == 20


# ============ band multiplier ============
async def test_band_multiplier_below_seven(db_session):
    site_settings._cache.clear()
    assert await streaks.get_band_multiplier(db_session, 0) == Decimal("1.0")
    assert await streaks.get_band_multiplier(db_session, 6) == Decimal("1.0")


async def test_band_multiplier_uses_highest_met_band(db_session):
    _seed_multiplier(db_session, 7, "1.1")
    _seed_multiplier(db_session, 14, "1.2")
    _seed_multiplier(db_session, 30, "1.5")
    await db_session.commit()
    site_settings._cache.clear()

    assert await streaks.get_band_multiplier(db_session, 7) == Decimal("1.1")
    assert await streaks.get_band_multiplier(db_session, 13) == Decimal("1.1")
    site_settings._cache.clear()
    assert await streaks.get_band_multiplier(db_session, 14) == Decimal("1.2")
    site_settings._cache.clear()
    assert await streaks.get_band_multiplier(db_session, 29) == Decimal("1.2")
    site_settings._cache.clear()
    assert await streaks.get_band_multiplier(db_session, 30) == Decimal("1.5")
    site_settings._cache.clear()
    assert await streaks.get_band_multiplier(db_session, 100) == Decimal("1.5")


async def test_band_multiplier_falls_back_to_defaults(db_session):
    """No seeded rows → service-level defaults (1.0 across the board)."""
    site_settings._cache.clear()
    assert await streaks.get_band_multiplier(db_session, 30) == Decimal("1.0")


# ============ milestone bonus ============
async def test_seven_day_milestone_pays_bonus(make_user, db_session):
    """Crossing into 7 pays STREAK_7_DAY_BONUS (default 5) as type=earned
    with reference_type=streak_bonus_7, bumping credits + total_credits_earned."""
    today = utcnow().date()
    user = await make_user(
        telegram_id=30100, current_streak=6, longest_streak=6,
        last_engagement_date=today - timedelta(days=1),
    )
    site_settings._cache.clear()

    out = await streaks.apply_streak_for_settlement(db_session, user)
    await db_session.commit()
    assert out["new_streak"] == 7
    assert out["crossed_threshold"] == 7
    assert out["bonus_awarded"] == Decimal("5")
    await db_session.refresh(user)
    assert user.credits == Decimal("5")
    assert user.total_credits_earned == Decimal("5")

    rows = (await db_session.execute(
        select(Transaction).where(
            Transaction.user_id == user.id,
            Transaction.type == TransactionType.EARNED,
        )
    )).scalars().all()
    assert len(rows) == 1
    assert rows[0].amount == Decimal("5")
    assert rows[0].reference_type == "streak_bonus_7"
    assert rows[0].idempotency_key == f"streak_bonus:{user.id}:7"


async def test_milestone_bonus_is_idempotent(make_user, db_session):
    """Re-running settlement at the same threshold (e.g. a retried batch)
    MUST NOT double-pay. Second call returns bonus_awarded=0."""
    today = utcnow().date()
    user = await make_user(
        telegram_id=30101, current_streak=6, longest_streak=6,
        last_engagement_date=today - timedelta(days=1),
    )
    site_settings._cache.clear()

    out1 = await streaks.apply_streak_for_settlement(db_session, user)
    await db_session.commit()
    assert out1["bonus_awarded"] == Decimal("5")

    # simulate the same-day re-run path: same UTC day, same threshold.
    # The same-day branch returns incremented=False (no bonus).
    out2 = await streaks.apply_streak_for_settlement(db_session, user)
    await db_session.commit()
    assert out2["incremented"] is False
    assert out2["bonus_awarded"] == Decimal("0")

    await db_session.refresh(user)
    assert user.credits == Decimal("5")  # NOT 10


async def test_day_eight_does_not_repay_seven_bonus(make_user, db_session):
    """Streak goes 7→8 the next day — no new bonus (the 7-day band was
    already paid). Crossed_threshold is None."""
    today = utcnow().date()
    user = await make_user(
        telegram_id=30102, current_streak=7, longest_streak=7,
        last_engagement_date=today - timedelta(days=1),
        credits=Decimal("5"), total_credits_earned=Decimal("5"),
    )
    site_settings._cache.clear()

    out = await streaks.apply_streak_for_settlement(db_session, user)
    await db_session.commit()
    assert out["new_streak"] == 8
    assert out["crossed_threshold"] is None
    assert out["bonus_awarded"] == Decimal("0")
    await db_session.refresh(user)
    assert user.credits == Decimal("5")  # untouched


async def test_fourteen_day_milestone_pays(make_user, db_session):
    today = utcnow().date()
    user = await make_user(
        telegram_id=30103, current_streak=13, longest_streak=13,
        last_engagement_date=today - timedelta(days=1),
    )
    site_settings._cache.clear()

    out = await streaks.apply_streak_for_settlement(db_session, user)
    await db_session.commit()
    assert out["new_streak"] == 14
    assert out["crossed_threshold"] == 14
    assert out["bonus_awarded"] == Decimal("6")  # default STREAK_14_DAY_BONUS
    await db_session.refresh(user)
    assert user.credits == Decimal("6")


async def test_thirty_day_milestone_pays(make_user, db_session):
    today = utcnow().date()
    user = await make_user(
        telegram_id=30104, current_streak=29, longest_streak=29,
        last_engagement_date=today - timedelta(days=1),
    )
    site_settings._cache.clear()

    out = await streaks.apply_streak_for_settlement(db_session, user)
    await db_session.commit()
    assert out["new_streak"] == 30
    assert out["crossed_threshold"] == 30
    assert out["bonus_awarded"] == Decimal("10")  # default STREAK_30_DAY_BONUS
    await db_session.refresh(user)
    assert user.credits == Decimal("10")


async def test_bonus_value_pulled_from_site_setting(make_user, db_session):
    """An admin can tune STREAK_7_DAY_BONUS live — the new value is used on
    the next milestone crossing."""
    _seed_bonus(db_session, 7, 42)
    await db_session.commit()
    site_settings._cache.clear()

    today = utcnow().date()
    user = await make_user(
        telegram_id=30105, current_streak=6, longest_streak=6,
        last_engagement_date=today - timedelta(days=1),
    )
    out = await streaks.apply_streak_for_settlement(db_session, user)
    await db_session.commit()
    assert out["bonus_awarded"] == Decimal("42")
    await db_session.refresh(user)
    assert user.credits == Decimal("42")


async def test_zero_bonus_setting_skips_credit(make_user, db_session):
    """STREAK_7_DAY_BONUS=0 must NOT write a zero-amount ledger row
    (transaction_amount_nonzero would fail) — bonus_awarded is 0, no row."""
    _seed_bonus(db_session, 7, 0)
    await db_session.commit()
    site_settings._cache.clear()

    today = utcnow().date()
    user = await make_user(
        telegram_id=30106, current_streak=6, longest_streak=6,
        last_engagement_date=today - timedelta(days=1),
    )
    out = await streaks.apply_streak_for_settlement(db_session, user)
    await db_session.commit()
    assert out["new_streak"] == 7
    assert out["crossed_threshold"] == 7
    assert out["bonus_awarded"] == Decimal("0")
    await db_session.refresh(user)
    assert user.credits == Decimal("0")  # streak bumped, no bonus paid

    rows = (await db_session.execute(
        select(Transaction).where(Transaction.user_id == user.id)
    )).scalars().all()
    assert rows == []  # no zero-amount row written
