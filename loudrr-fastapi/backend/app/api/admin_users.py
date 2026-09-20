"""Per-user admin detail (the Users tab's drill-down).

The list endpoint in api/admin.py answers "who is there"; this answers "what
has this account actually done" — the history an admin needs BEFORE moving
someone's balance. Five read endpoints, all admin-gated, all paginated the
same way ({rows, total}) so the shared <Pagination/> works everywhere:

  GET /api/admin/users/{id}/                identity, flags, score, credits,
                                            xp, activity, waitlist, referrals
  GET /api/admin/users/{id}/transactions/   the karma ledger
  GET /api/admin/users/{id}/posts/          posts they submitted
  GET /api/admin/users/{id}/engagements/    posts they engaged with
  GET /api/admin/users/{id}/audit/          admin actions taken ON them,
                                            joined to the acting admin's handle

`spendable_headroom` = min(credits, total_credits_earned - total_credits_spent)
is the number that matters when granting: the DB's earned_ge_spent check means
karma above that headroom cannot actually be spent, so surfacing it makes any
future ledger drift visible instead of mysterious.
"""
import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select

from app.core.deps import require_admin
from app.db.session import get_session
from app.models.audit_log import AuditLog
from app.models.engagement import Engagement
from app.models.post import Post
from app.models.transaction import Transaction
from app.models.user import User
from app.models.waitlist_entry import WaitlistEntry
from app.models.x_verification_request import XVerificationRequest
from app.repositories.user import UserRepository
from app.services import sponsors as sponsors_svc
from app.services import tier as tier_svc

# mounted onto api/admin.py's router (prefix "/api/admin") → /api/admin/users/…
router = APIRouter(prefix="/users", tags=["admin"])


def _iso(dt):
    return dt.isoformat() if dt else None


async def _get_user(db, user_id: uuid.UUID) -> User:
    return await UserRepository(db).get_or_404(id=user_id, label="user")


async def _count(db, stmt) -> int:
    return int((await db.execute(stmt)).scalar_one())


# ---------------------------------------------------------------------------
# GET /api/admin/users/{user_id}/
# ---------------------------------------------------------------------------
@router.get("/{user_id}/")
async def user_detail(
    user_id: uuid.UUID,
    _admin: User = Depends(require_admin),
    db=Depends(get_session),
):
    u = await _get_user(db, user_id)

    credits = Decimal(u.credits or 0)
    earned = Decimal(u.total_credits_earned or 0)
    spent = Decimal(u.total_credits_spent or 0)
    # karma the user can actually spend: the balance, further capped by the
    # lifetime earned-minus-spent room the DB's earned_ge_spent check leaves.
    headroom = min(credits, earned - spent)
    if headroom < 0:
        headroom = Decimal(0)

    # activity counters that need a query (the denormalised ones live on User)
    posts_by_status = dict(
        (
            await db.execute(
                select(Post.status, func.count(Post.id))
                .where(Post.user_id == u.id)
                .group_by(Post.status)
            )
        ).all()
    )
    eng_total = await _count(
        db, select(func.count(Engagement.id)).where(Engagement.user_id == u.id)
    )
    eng_pending = await _count(
        db,
        select(func.count(Engagement.id)).where(
            Engagement.user_id == u.id,
            Engagement.verified.is_(False),
            Engagement.credit_granted.is_(False),
        ),
    )
    escrow_locked = (
        await db.execute(
            select(func.coalesce(func.sum(Post.escrow), 0)).where(
                Post.user_id == u.id, Post.status == "active"
            )
        )
    ).scalar_one()

    # waitlist entry — either the one that created this user, or the one that
    # shares their telegram_id (entries created before approval linked back)
    entry = (
        await db.execute(
            select(WaitlistEntry)
            .where(
                (WaitlistEntry.created_user_id == u.id)
                | (WaitlistEntry.telegram_id == u.telegram_id)
            )
            .order_by(WaitlistEntry.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    # WaitlistEntry.referrer_id points at a USER (the referrer had to already
    # be approved), so this count works whether or not this user has an entry
    referrals_made = await _count(
        db,
        select(func.count(WaitlistEntry.id)).where(WaitlistEntry.referrer_id == u.id),
    )

    waitlist_out = None
    referred_by = ""
    if entry is not None:
        referred_by = entry.referral_code_used or ""
        waitlist_out = {
            "id": str(entry.id),
            "status": entry.status,
            "x_username": entry.x_username,
            "x_verified": entry.x_verified,
            "region": entry.region,
            "niche": entry.niche,
            "score": entry.score,
            "created_at": _iso(entry.created_at),
            "approved_at": _iso(entry.approved_at),
            "rejection_reason": entry.rejection_reason or "",
        }

    pending_x = await _count(
        db,
        select(func.count(XVerificationRequest.id)).where(
            XVerificationRequest.user_id == u.id,
            XVerificationRequest.status == "PENDING",
        ),
    )

    return {
        "id": str(u.id),
        "telegram_id": u.telegram_id,
        "telegram_username": u.telegram_username or "",
        "x_username": u.x_username or "",
        "display_name": u.display_name or "",
        "referral_code": u.referral_code or "",
        "created_at": _iso(u.created_at),
        "is_platform_account": str(u.id) == str(sponsors_svc.PLATFORM_USER_ID),
        "flags": {
            "role": u.role or "",
            "is_banned": u.is_banned,
            "is_whitelisted": u.is_whitelisted,
            "x_verified": u.x_verified,
            "x_verified_at": _iso(u.x_verified_at),
            "loud_access": u.loud_access,
            "pending_x_verification": pending_x > 0,
            "pending_claimed_x_username": u.pending_claimed_x_username or "",
        },
        "score": {
            "tweetscout_score": float(u.tweetscout_score or 0),
            "tier": tier_svc.tier_for(u.tweetscout_score or 0),
            "multiplier": float(tier_svc.multiplier_for(u.tweetscout_score or 0)),
            "score_updated_at": _iso(u.tweetscout_last_updated),
        },
        "credits": {
            "balance": float(credits),
            "total_earned": float(earned),
            "total_spent": float(spent),
            # min(balance, earned - spent) — karma above this cannot be spent
            "spendable_headroom": float(headroom),
            "daily_earned": float(u.daily_credits_earned or 0),
            "daily_reset_at": _iso(u.daily_earned_reset_at),
            "escrow_locked": float(escrow_locked or 0),
        },
        "xp": {
            "sponsored_xp": int(u.sponsored_xp or 0),
            "total_sponsored_xp_earned": int(u.total_sponsored_xp_earned or 0),
            "sponsored_engagements": int(u.sponsored_engagements or 0),
        },
        "activity": {
            "total_engagements": int(u.total_engagements or 0),
            "total_posts": int(u.total_posts or 0),
            "engagements_recorded": eng_total,
            "engagements_pending": eng_pending,
            "posts_active": int(posts_by_status.get("active", 0)),
            "posts_completed": int(posts_by_status.get("completed", 0)),
            "posts_cancelled": int(posts_by_status.get("cancelled", 0)),
            "current_streak": int(u.current_streak or 0),
            "longest_streak": int(u.longest_streak or 0),
            "last_engagement_date": (
                u.last_engagement_date.isoformat() if u.last_engagement_date else None
            ),
            "honesty_score": int(u.honesty_score or 0),
        },
        "waitlist": waitlist_out,
        "referrals": {
            "code": u.referral_code or "",
            "referred_by_code": referred_by,
            "referrals_made": referrals_made,
        },
    }


# ---------------------------------------------------------------------------
# GET /api/admin/users/{user_id}/transactions/
# ---------------------------------------------------------------------------
@router.get("/{user_id}/transactions/")
async def user_transactions(
    user_id: uuid.UUID,
    limit: int = Query(default=25, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _admin: User = Depends(require_admin),
    db=Depends(get_session),
):
    await _get_user(db, user_id)
    total = await _count(
        db, select(func.count(Transaction.id)).where(Transaction.user_id == user_id)
    )
    rows = (
        await db.execute(
            select(Transaction)
            .where(Transaction.user_id == user_id)
            .order_by(Transaction.created_at.desc(), Transaction.id.desc())
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()
    return {
        "total": total,
        "rows": [
            {
                "id": str(t.id),
                "type": t.type.value if hasattr(t.type, "value") else str(t.type),
                "amount": float(t.amount),
                "balance_after": float(t.balance_after),
                "description": t.description or "",
                "reference_id": str(t.reference_id) if t.reference_id else None,
                "reference_type": t.reference_type or "",
                "created_at": _iso(t.created_at),
            }
            for t in rows
        ],
    }


# ---------------------------------------------------------------------------
# GET /api/admin/users/{user_id}/posts/
# ---------------------------------------------------------------------------
@router.get("/{user_id}/posts/")
async def user_posts(
    user_id: uuid.UUID,
    limit: int = Query(default=25, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _admin: User = Depends(require_admin),
    db=Depends(get_session),
):
    await _get_user(db, user_id)
    total = await _count(db, select(func.count(Post.id)).where(Post.user_id == user_id))
    rows = (
        await db.execute(
            select(Post)
            .where(Post.user_id == user_id)
            .order_by(Post.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()
    post_ids = [p.id for p in rows]
    eng_counts: dict = {}
    if post_ids:
        eng_counts = dict(
            (
                await db.execute(
                    select(Engagement.post_id, func.count(Engagement.id))
                    .where(Engagement.post_id.in_(post_ids))
                    .group_by(Engagement.post_id)
                )
            ).all()
        )
    return {
        "total": total,
        "rows": [
            {
                "id": str(p.id),
                "x_link": p.x_link,
                "tweet_text": (p.tweet_text or "")[:200],
                "status": p.status,
                "is_sponsored": p.is_sponsored,
                "escrow": float(p.escrow),
                "initial_escrow": float(p.initial_escrow),
                "engagements": int(eng_counts.get(p.id, 0)),
                "created_at": _iso(p.created_at),
                "completed_at": _iso(p.completed_at),
            }
            for p in rows
        ],
    }


# ---------------------------------------------------------------------------
# GET /api/admin/users/{user_id}/engagements/
# ---------------------------------------------------------------------------
@router.get("/{user_id}/engagements/")
async def user_engagements(
    user_id: uuid.UUID,
    limit: int = Query(default=25, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _admin: User = Depends(require_admin),
    db=Depends(get_session),
):
    await _get_user(db, user_id)
    total = await _count(
        db, select(func.count(Engagement.id)).where(Engagement.user_id == user_id)
    )
    rows = (
        await db.execute(
            select(Engagement, Post)
            .join(Post, Post.id == Engagement.post_id)
            .where(Engagement.user_id == user_id)
            .order_by(Engagement.clicked_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return {
        "total": total,
        "rows": [
            {
                "id": str(e.id),
                "post_id": str(e.post_id),
                "post_link": p.x_link,
                "post_author": p.tweet_author_username or "",
                "is_sponsored": p.is_sponsored,
                "verified": e.verified,
                "credit_granted": e.credit_granted,
                "like_verified": e.like_verified,
                "reply_verified": e.reply_verified,
                "clicked_at": _iso(e.clicked_at),
            }
            for e, p in rows
        ],
    }


# ---------------------------------------------------------------------------
# GET /api/admin/users/{user_id}/audit/
# ---------------------------------------------------------------------------
@router.get("/{user_id}/audit/")
async def user_audit(
    user_id: uuid.UUID,
    limit: int = Query(default=25, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _admin: User = Depends(require_admin),
    db=Depends(get_session),
):
    """Admin actions taken ON this user, newest first, with the acting admin's
    handle joined in — an actor uuid alone tells nobody anything."""
    await _get_user(db, user_id)
    total = await _count(
        db,
        select(func.count(AuditLog.id)).where(
            AuditLog.target_type == "user", AuditLog.target_id == user_id
        ),
    )
    actor = User.__table__.alias("actor")
    rows = (
        await db.execute(
            select(AuditLog, actor.c.telegram_username, actor.c.x_username, actor.c.role)
            .outerjoin(actor, actor.c.id == AuditLog.actor_id)
            .where(AuditLog.target_type == "user", AuditLog.target_id == user_id)
            .order_by(AuditLog.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return {
        "total": total,
        "rows": [
            {
                "id": str(log.id),
                "action": log.action,
                "detail": log.detail or {},
                "actor_id": str(log.actor_id) if log.actor_id else None,
                "actor_handle": tg or x or "",
                "actor_role": role or "",
                "created_at": _iso(log.created_at),
            }
            for log, tg, x, role in rows
        ],
    }
