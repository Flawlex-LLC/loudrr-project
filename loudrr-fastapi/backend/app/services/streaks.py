"""Streak system (Ch — port of Django core/services/engagements.py:_update_streak).

Rules (Django parity + the documented port-plan additions):

* **When**: one bump per UTC day per user, fired from `settlement.settle()`
  after the per-engagement loop, gated on at-least-one award.
* **Body**: first engagement ever → 1; same UTC day → no-op; consecutive UTC
  day (last == today - 1) → +1; gap → reset to 1.
* **Longest** updates the running max whenever current exceeds it.
* **Bonus**: a one-shot flat karma grant the moment the streak transitions
  UP across 7 / 14 / 30. Idempotency key `streak_bonus:<user>:<threshold>`
  so re-settling a batch cannot double-pay. Bonus is granted via
  `CreditService.admin_grant` (free of the daily cap, like the Django
  reference's "bonus karma at milestone").
* **Multiplier**: the band's multiplier (1.0 default) is the bonus stacked
  on the tier multiplier at karma_for time. Read via `get_band_multiplier()`
  which is consulted by `_settle_passed` before computing karma.
* **Reset cron**: `reset_broken_streaks` zeroes current_streak for users
  whose last_engagement_date < (today - 1) and current_streak > 0. The
  lazy in-engagement reset (in apply_streak_for_settlement) handles the
  same case for users who come back before the cron runs.

This service NEVER commits — the caller (settlement, the cron) owns the
commit/rollback boundary so the streak bump + bonus + outbox event land in
one atomic transaction.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select, update

from app.core.time_utils import utcnow
from app.models.transaction import Transaction, TransactionType
from app.models.user import User
from app.services.site_settings import get_setting

logger = logging.getLogger(__name__)


# Streak band → (SiteSetting multiplier key, SiteSetting bonus key, default mul,
# default bonus). 30 first so the highest-met band wins (highest threshold first
# scan).
_BANDS: tuple[tuple[int, str, str, Decimal, int], ...] = (
    (30, "STREAK_30_DAY_MULTIPLIER", "STREAK_30_DAY_BONUS", Decimal("1.0"), 10),
    (14, "STREAK_14_DAY_MULTIPLIER", "STREAK_14_DAY_BONUS", Decimal("1.0"), 6),
    (7, "STREAK_7_DAY_MULTIPLIER", "STREAK_7_DAY_BONUS", Decimal("1.0"), 5),
)

# The thresholds that pay out a bonus (ordered low→high so we can dedup the
# transition: "the streak just hit exactly N").
THRESHOLDS: tuple[int, ...] = (7, 14, 30)


async def get_band_multiplier(db, current_streak: int) -> Decimal:
    """Return the streak multiplier for a streak length, as a Decimal.

    Highest met band wins (>=30 over >=14 over >=7). Below 7 returns 1.0 (no
    boost). The values are read from SiteSetting at request time so an admin
    can tune them live; missing rows fall through to the hardcoded defaults.
    """
    s = int(current_streak or 0)
    for threshold, mul_key, _bonus_key, default_mul, _default_bonus in _BANDS:
        if s >= threshold:
            raw = await get_setting(db, mul_key, default=default_mul)
            return raw if isinstance(raw, Decimal) else Decimal(str(raw))
    return Decimal("1.0")


async def _bonus_amount_for(db, threshold: int) -> Decimal:
    """The flat karma bonus for crossing `threshold` UP, as Decimal."""
    for t, _mul_key, bonus_key, _default_mul, default_bonus in _BANDS:
        if t == threshold:
            raw = await get_setting(db, bonus_key, default=default_bonus)
            return raw if isinstance(raw, Decimal) else Decimal(str(raw))
    return Decimal("0")


async def apply_streak_for_settlement(db, user: User) -> dict:
    """Bump `user`'s streak for today's settlement and pay any milestone bonus.

    Returns ``{incremented, new_streak, crossed_threshold, bonus_awarded}``:

      - ``incremented``       True if current_streak actually changed.
      - ``new_streak``        The streak value after the call.
      - ``crossed_threshold`` 7/14/30 if the streak *just* hit that band, else
                              None. The transition is UP-only (prev < N == new).
      - ``bonus_awarded``     Decimal — the karma added on the threshold cross
                              (0 if none crossed or admin_grant returned None).

    Mirrors Django ``_update_streak`` (core/services/engagements.py:261-282)
    using utcnow().date(). No commit here — the caller owns the transaction.
    """
    today = utcnow().date()
    last = user.last_engagement_date
    prev = int(user.current_streak or 0)

    if last is None:
        new_streak = 1
    elif last == today:
        # already counted today — Django parity ("no-op" branch). Return early
        # so we don't re-trigger the milestone bonus on the second engagement
        # of the same UTC day.
        return {
            "incremented": False,
            "new_streak": prev,
            "crossed_threshold": None,
            "bonus_awarded": Decimal("0"),
        }
    elif last == today - timedelta(days=1):
        new_streak = prev + 1
    else:
        # gap — Django resets to 1 (the user broke their streak but is back)
        new_streak = 1

    user.current_streak = new_streak
    user.last_engagement_date = today
    if new_streak > int(user.longest_streak or 0):
        user.longest_streak = new_streak

    crossed: int | None = None
    bonus_awarded = Decimal("0")
    for threshold in THRESHOLDS:
        if prev < threshold <= new_streak:
            # `<=` on the new value handles both the natural ++ over the
            # threshold AND a gap-reset that lands above it (won't happen with
            # our +=1 / reset-to-1 rules but stays correct if the rules change)
            crossed = threshold

    if crossed is not None:
        amount = await _bonus_amount_for(db, crossed)
        if amount > Decimal("0"):
            paid = await _grant_streak_bonus(
                db, user, amount=amount, threshold=crossed,
            )
            if paid is not None:
                bonus_awarded = paid

    return {
        "incremented": True,
        "new_streak": new_streak,
        "crossed_threshold": crossed,
        "bonus_awarded": bonus_awarded,
    }


async def _grant_streak_bonus(
    db, user: User, *, amount: Decimal, threshold: int,
) -> Decimal | None:
    """Credit `amount` karma to `user` as a free-of-cap, dedup'd streak bonus.

    Bypasses the daily earn cap (a milestone bonus is platform-funded — not
    user-cap-budgeted). Idempotency key is ``streak_bonus:<uuid>:<threshold>``
    so a re-settled batch (or a manual re-run) MUST return the existing row
    without double-paying. Bumps both ``credits`` and ``total_credits_earned``
    (matching the Django reference's TransactionType.EARNED semantics) so the
    bonus shows up in lifetime stats without breaking the
    ``earned_ge_spent`` constraint when the user later spends.
    """
    idem = f"streak_bonus:{user.id}:{threshold}"
    existing = (
        await db.execute(
            select(Transaction).where(
                Transaction.user_id == user.id,
                Transaction.type == TransactionType.EARNED,
                Transaction.idempotency_key == idem,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return None  # already paid — caller should treat as no-op

    # lock the user row so two concurrent settlements can't race the credit
    # update. populate_existing makes the lock effective against the identity
    # map (matches CreditService.earn's pattern).
    locked = (
        await db.execute(
            select(User).where(User.id == user.id)
            .with_for_update().execution_options(populate_existing=True)
        )
    ).scalar_one()
    locked.credits += amount
    locked.total_credits_earned += amount

    txn = Transaction(
        user_id=locked.id,
        type=TransactionType.EARNED,
        amount=amount,
        balance_after=locked.credits,
        reference_type=f"streak_bonus_{threshold}",
        idempotency_key=idem,
        description=f"Streak bonus: {threshold}-day milestone",
    )
    db.add(txn)
    await db.flush()
    return amount


async def reset_broken_streaks(db) -> int:
    """Zero current_streak for users whose streak has lapsed.

    Run from the daily 00:05 UTC cron (see app/tasks/worker.py). Matches the
    semantic of the Django _update_streak "gap" branch but applied proactively
    so the rules predicates and the mini-app counter don't show a stale
    streak for users who didn't engage today. Returns the rowcount.
    """
    cutoff = utcnow().date() - timedelta(days=1)
    result = await db.execute(
        update(User)
        .where(
            User.current_streak > 0,
            User.last_engagement_date.is_not(None),
            User.last_engagement_date < cutoff,
        )
        .values(current_streak=0)
    )
    await db.commit()
    return result.rowcount or 0
