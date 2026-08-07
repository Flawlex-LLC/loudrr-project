"""Phase 2 of verification — atomic settlement, NO external calls (spec §5.2).

One DB transaction. Locks the user, then all involved posts (sorted by id to
avoid deadlocks), then the engagements. For each PASSED engagement (in its own
savepoint): compute tiered karma, cap it at remaining escrow (partial payment),
deduct escrow, credit the engager (idempotent on engagement id), mark it
verified+credited, auto-complete the post if escrow hits 0. For each FAILED
engagement: delete it so the user can re-engage cleanly. Failures drop the
user's honesty score.
"""
import logging
import math
from decimal import Decimal

from sqlalchemy import select

from app.core.time_utils import utcnow
from app.models.engagement import Engagement
from app.models.outbox_event import OutboxEvent, OutboxEventType, OutboxStatus
from app.models.post import Post
from app.models.user import User
from app.services import streaks, tier
from app.services.credits import CreditService
from app.services.site_settings import get_setting
from app.services.xp import XPService, get_xp_for_sponsored_engagement

logger = logging.getLogger(__name__)


async def _has_daily_cap_event_today(db, *, user_id, today_iso: str) -> bool:
    """Did we already queue (or send) a daily_cap_reached event for this user
    in today's UTC window? Cheap dedup so the user gets at most one Telegram
    card per UTC day even if they keep trying to earn after hitting the cap."""
    row = (
        await db.execute(
            select(OutboxEvent.id).where(
                OutboxEvent.event_type == OutboxEventType.DAILY_CAP_REACHED.value,
                OutboxEvent.status.in_((
                    OutboxStatus.PENDING.value,
                    OutboxStatus.PROCESSING.value,
                    OutboxStatus.SENT.value,
                )),
                OutboxEvent.payload["user_id"].astext == str(user_id),
                OutboxEvent.payload["date"].astext == today_iso,
            ).limit(1)
        )
    ).first()
    return row is not None


async def _settle_passed(db, user, post, engagement, r, base, credits, streak_multiplier):
    """Settle one PASSED engagement → (status, amount). status is one of
    'awarded' | 'partial' | 'skipped' | 'error'.

    ``streak_multiplier`` (Decimal) is the active streak band's multiplier
    (1.0 = no boost). Stacked on the tier multiplier inside karma_for.
    """
    now = utcnow()

    if engagement is None or engagement.credit_granted or post is None:
        return ("skipped", Decimal("0"))

    if post.status != "active":
        engagement.verified = True
        engagement.reply_verified = r.reply_verified
        engagement.like_verified = r.like_verified
        engagement.credit_granted = False
        engagement.verification_data = {"verified_at": now.isoformat(), "result": "skipped_post_inactive"}
        return ("skipped", Decimal("0"))

    karma, multiplier = tier.karma_for(
        base, user.tweetscout_score or 0, streak_multiplier=streak_multiplier,
    )

    # partial payment — never deduct more than the escrow holds
    if post.escrow < karma:
        if post.escrow <= 0:
            engagement.verified = True
            engagement.credit_granted = False
            engagement.verification_data = {"verified_at": now.isoformat(), "result": "skipped_no_escrow"}
            return ("skipped", Decimal("0"))
        karma = post.escrow

    # daily cap — skip the whole award (preserve escrow) if it won't fit
    if not await credits.can_earn(karma):
        engagement.verified = True
        engagement.credit_granted = False
        engagement.verification_data = {"verified_at": now.isoformat(), "result": "skipped_daily_cap"}
        # one-per-UTC-day Telegram nudge so the user knows why they aren't
        # earning. Dedup on (user_id, payload.date) — see _has_daily_cap_event_today.
        if user.telegram_id is not None:
            today_iso = now.date().isoformat()
            if not await _has_daily_cap_event_today(
                db, user_id=user.id, today_iso=today_iso,
            ):
                from app.services.outbox import OutboxService
                cap = await get_setting(db, "DAILY_EARN_CAP")
                await OutboxService.queue_daily_cap_reached(
                    db, user_id=user.id, telegram_id=user.telegram_id,
                    cap=cap, daily_earned=user.daily_credits_earned,
                    date=today_iso,
                )
        return ("skipped", Decimal("0"))

    is_partial = karma < (base * multiplier)
    try:
        async with db.begin_nested():  # savepoint — one failure can't break the batch
            post.escrow = post.escrow - karma
            await credits.earn(
                karma, idempotency_key=str(engagement.id), reference_id=engagement.id,
                description=f"Engagement verified (x{multiplier})", commit=False,
            )
            engagement.verified = True
            engagement.reply_verified = r.reply_verified
            engagement.like_verified = r.like_verified
            engagement.credit_granted = True
            engagement.verification_data = {
                "verified_at": now.isoformat(), "result": "awarded",
                "amount": str(karma), "multiplier": str(multiplier),
            }
            if post.escrow <= 0:  # escrow depleted → auto-complete
                post.status = "completed"
                post.completed_at = now
                # notify the poster their post is done — fire-and-forget via
                # the outbox so the savepoint can still be rolled back on error
                from sqlalchemy import func
                from app.repositories.user import UserRepository
                from app.services.outbox import OutboxService
                poster = await UserRepository(db).get(id=post.user_id)
                if poster is not None and poster.telegram_id is not None:
                    eng_count = int(
                        (
                            await db.execute(
                                select(func.count())
                                .select_from(Engagement)
                                .where(
                                    Engagement.post_id == post.id,
                                    Engagement.credit_granted.is_(True),
                                )
                            )
                        ).scalar_one()
                    )
                    # include the engagement we just settled in this savepoint
                    await OutboxService.queue_post_completed(
                        db, post_id=post.id, user_id=post.user_id,
                        telegram_id=poster.telegram_id,
                        total_engagements=eng_count + 1,
                    )
            user.total_engagements += 1
            # Sponsored-XP top-up (Django parity — core/services/settlement.py:383-395).
            # Sponsored != free: the creator's escrow was already debited the karma
            # above; this is the ADDITIONAL platform-funded XP nudge to the engager.
            # Lives inside the savepoint so an XP write failure (e.g. tripped
            # sponsored_xp_non_negative check) unwinds the karma earn too — stricter
            # than Django's "non-critical" swallow but safer for accounting.
            if post.is_sponsored:
                xp_amount = await get_xp_for_sponsored_engagement(db)
                if xp_amount > 0:
                    await XPService(db, user).earn_from_sponsored(
                        amount=xp_amount,
                        post_id=post.id,
                        description="Sponsored engagement reward",
                    )
            await db.flush()
    except Exception:
        logger.exception("settlement failed for engagement %s", engagement.id)
        engagement.verified = True
        engagement.credit_granted = False
        return ("error", Decimal("0"))

    return ("partial" if is_partial else "awarded", karma)


async def settle(db, *, user_id, results) -> dict:
    """Phase 2. Returns {total_awarded: Decimal, new_balance: Decimal}."""
    if not results:
        return {"total_awarded": Decimal("0"), "new_balance": Decimal("0")}

    # lock order: user → posts (sorted) → engagements
    user = (
        await db.execute(
            select(User).where(User.id == user_id)
            .with_for_update().execution_options(populate_existing=True)
        )
    ).scalar_one()

    # defensive: a user banned after queueing but before settlement runs earns
    # nothing — release the lock and award zero (queue_claim already blocks the
    # normal path; this guards the ban-mid-flight race)
    if user.is_banned:
        await db.commit()
        return {"total_awarded": Decimal("0"), "new_balance": user.credits}

    post_ids = sorted({r.post_id for r in results})
    posts = {}
    if post_ids:
        rows = (
            await db.execute(
                select(Post).where(Post.id.in_(post_ids)).order_by(Post.id)
                .with_for_update().execution_options(populate_existing=True)
            )
        ).scalars().all()
        posts = {p.id: p for p in rows}

    eng_ids = [r.engagement_id for r in results]
    engs = {}
    if eng_ids:
        rows = (
            await db.execute(
                select(Engagement)
                .where(Engagement.id.in_(eng_ids), Engagement.user_id == user_id)
                .with_for_update().execution_options(populate_existing=True)
            )
        ).scalars().all()
        engs = {e.id: e for e in rows}

    base = Decimal(str(await get_setting(db, "CREDIT_PER_ENGAGEMENT", 1)))
    credits = CreditService(db, user)
    # Streak multiplier — the band active BEFORE this batch's increment, so an
    # engagement settled on day-6 still uses 1.0 even though it might tip the
    # streak to 7. The day-7 boost kicks in on the NEXT batch (matches Django
    # semantics: the streak bonus is paid on the *transition*, not retro-applied).
    streak_multiplier = await streaks.get_band_multiplier(db, user.current_streak)
    total_awarded = Decimal("0")
    failures = 0
    awarded_count = 0

    for r in results:
        if r.passed:
            status, amount = await _settle_passed(
                db, user, posts.get(r.post_id), engs.get(r.engagement_id), r, base, credits,
                streak_multiplier,
            )
            if status in ("awarded", "partial"):
                total_awarded += amount
                awarded_count += 1
        else:
            eng = engs.get(r.engagement_id)
            if eng is not None:
                await db.delete(eng)  # failed → delete so the user can re-engage
            failures += 1

    if failures > 0:
        drop = max(1, math.ceil(failures / 2))
        user.honesty_score = max(0, user.honesty_score - drop)

    # Streak bump (once per batch, gated on "at least one award"). Runs inside
    # the same atomic transaction as the karma writes so the streak state and
    # the credit ledger commit together. Any milestone bonus is folded into
    # total_awarded so the claim_completed Telegram card reflects the real
    # total the user just earned.
    bonus_awarded = Decimal("0")
    streak_milestone = None
    new_streak = int(user.current_streak or 0)
    if awarded_count > 0:
        outcome = await streaks.apply_streak_for_settlement(db, user)
        bonus_awarded = outcome["bonus_awarded"]
        streak_milestone = outcome["crossed_threshold"]
        new_streak = outcome["new_streak"]
        total_awarded += bonus_awarded

        # Queue the milestone Telegram card in THIS transaction so it commits
        # with the karma writes. Dedup'd by idempotency_key so a re-run cannot
        # double-notify.
        if streak_milestone is not None and user.telegram_id is not None:
            from app.services.outbox import OutboxService
            await OutboxService.queue_streak_milestone(
                db, user_id=user.id, telegram_id=user.telegram_id,
                streak=new_streak, threshold=streak_milestone,
                bonus=bonus_awarded,
            )

    await db.commit()
    return {
        "total_awarded": total_awarded, "new_balance": user.credits,
        "streak_bonus": bonus_awarded, "streak_milestone": streak_milestone,
        "new_streak": new_streak,
    }
