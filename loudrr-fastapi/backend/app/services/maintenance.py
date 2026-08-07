"""Periodic-maintenance logic (Ch16) — the bodies of the scheduled tasks.

Kept as plain async functions taking a session, so they're testable without
Redis/arq. The arq worker (app/tasks/worker.py) wraps each in its own session.
"""
import logging
import uuid
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func, select, update

from app.core.time_utils import utcnow
from app.models.post import Post
from app.models.transaction import Transaction
from app.models.user import User
from app.repositories.user import UserRepository
from app.services import posts as posts_svc
from app.services import streaks
from app.services.credits import CreditService
from app.services.outbox import OutboxService
from app.services.site_settings import get_setting

logger = logging.getLogger(__name__)

# Synthetic system actor id for decay penalties — decay isn't initiated by a
# real admin, but apply_penalty's audit row needs a non-null admin_id. The
# all-zero UUID is deliberately not a real user id (we never seed it), so any
# Transaction whose description says "Karma decay" + admin_id = nil-UUID is
# unambiguously a system action.
DECAY_SYSTEM_ACTOR_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")


async def reset_daily_credits(db) -> int:
    """Zero every user's daily earn counter (runs at midnight UTC)."""
    result = await db.execute(
        update(User).values(
            daily_credits_earned=Decimal("0"), daily_earned_reset_at=utcnow()
        )
    )
    await db.commit()
    return result.rowcount or 0


async def expire_old_posts(db) -> int:
    """Cancel + refund active posts older than POST_EXPIRY_HOURS (hourly)."""
    hours = await get_setting(db, "POST_EXPIRY_HOURS", 48)
    cutoff = utcnow() - timedelta(hours=int(hours))
    stale = (
        await db.execute(
            select(Post).where(Post.status == "active", Post.created_at < cutoff)
        )
    ).scalars().all()
    count = 0
    for post in stale:
        # capture the refund amount before cancel_post zeros the escrow
        refund_amount = post.escrow
        poster = await UserRepository(db).get(id=post.user_id)
        await posts_svc.cancel_post(db, post, refund=True)  # commits per post
        # queue the user-facing notification in its own follow-up txn (cancel_post
        # already committed). This is best-effort — the cancel/refund itself is
        # the source of truth; the outbox just tells the user.
        if poster is not None and poster.telegram_id is not None:
            await OutboxService.queue_post_expired(
                db, post_id=post.id, user_id=post.user_id,
                telegram_id=poster.telegram_id, refund_amount=refund_amount,
            )
            await db.commit()
        count += 1
    if count:
        logger.info("expire_old_posts: cancelled+refunded %s posts", count)
    return count


async def reset_broken_streaks(db) -> int:
    """Zero current_streak for users whose streak has lapsed (00:05 UTC daily).

    Thin pass-through to streaks.reset_broken_streaks so the cron's body lives
    next to the rest of the streak logic. Stronger-than-Django guarantee: the
    Django reference relies on the lazy in-engagement reset and shows a stale
    streak until the user next engages; this proactive cron keeps the rules
    predicates and the mini-app counter honest.
    """
    n = await streaks.reset_broken_streaks(db)
    if n:
        logger.info("reset_broken_streaks: zeroed %s lapsed streaks", n)
    return n


async def decay_inactive_karma(db) -> int:
    """Decay karma for users inactive for > KARMA_DECAY_THRESHOLD_DAYS (02:00 UTC daily).

    Inactivity signal: the user's most-recent Transaction.created_at, falling
    back to User.created_at when the user has never transacted. The FastAPI
    User model has no `last_active_at` column — adding one would require an
    Alembic migration plus write-amplification on every save; transactions
    already capture every economic event we care about (earn/spend/refund/
    admin_grant/apply_penalty), so this gives us decay without schema churn.

    Per-run formula: deduction = current_credits * KARMA_DECAY_RATE. Running
    once daily compounds for free — day N+1 reads the already-decayed
    balance. Floor of zero falls out of CreditService.apply_penalty's
    existing clamp + the DB-level `credits >= 0` CHECK constraint; we also
    pre-skip when the quantized deduction is below 0.0001 to avoid handing
    apply_penalty a sub-cent amount that it would refuse via its
    `amount > 0` guard (and that transaction_amount_nonzero would refuse too).

    Idempotent across same-day re-runs: the (user_id, type, idempotency_key)
    unique constraint with idempotency_key=f"karma_decay:{user_id}:{utc_date}"
    makes a same-day double-run return the existing row instead of decaying
    twice.
    """
    threshold_days = int(await get_setting(db, "KARMA_DECAY_THRESHOLD_DAYS"))
    rate = Decimal(str(await get_setting(db, "KARMA_DECAY_RATE")))
    if rate <= Decimal("0"):
        return 0  # decay disabled by config
    now = utcnow()
    cutoff = now - timedelta(days=threshold_days)
    today_iso = now.date().isoformat()

    # Find each eligible user's "last activity" via LEFT JOIN on transactions:
    # COALESCE(max(Transaction.created_at), User.created_at) is the inactivity
    # signal. Filter pre-aggregation conditions (is_banned, credits > 0) on
    # User, post-aggregation on the COALESCE so brand-new users with no
    # transactions still get a grace period from their User.created_at.
    last_activity = func.coalesce(
        func.max(Transaction.created_at), User.created_at
    )
    stmt = (
        select(User.id, last_activity.label("last_active"))
        .select_from(User)
        .outerjoin(Transaction, Transaction.user_id == User.id)
        .where(User.is_banned.is_(False), User.credits > Decimal("0"))
        .group_by(User.id, User.created_at)
        .having(last_activity < cutoff)
    )
    rows = (await db.execute(stmt)).all()

    decayed = 0
    for user_id, _last_active in rows:
        # Re-fetch each user so apply_penalty's row lock sees a fresh ORM
        # instance — and so a balance change between the SELECT above and
        # the penalty (e.g. a parallel earn) is reflected. Skip if the
        # quantized deduction is below the ledger's 4-dp resolution.
        user = await UserRepository(db).get(id=user_id)
        if user is None or user.credits <= Decimal("0"):
            continue
        decay_amount = (user.credits * rate).quantize(Decimal("0.0001"))
        if decay_amount < Decimal("0.0001"):
            continue
        idem = f"karma_decay:{user_id}:{today_iso}"
        txn = await CreditService(db, user).apply_penalty(
            amount=decay_amount,
            admin_id=DECAY_SYSTEM_ACTOR_ID,
            idempotency_key=idem,
            description="Karma decay (inactivity)",
        )
        if txn is not None:
            decayed += 1
    if decayed:
        logger.info("decay_inactive_karma: decayed %s users", decayed)
    return decayed
