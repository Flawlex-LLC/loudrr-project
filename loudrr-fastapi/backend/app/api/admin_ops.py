"""Admin → Operations: the things launch week breaks, made visible and fixable.

  GET  /api/admin/ops/health/                 gateway credits, sponsor feed, queues
  GET  /api/admin/ops/batches/                claim-verification batches
  POST /api/admin/ops/batches/{id}/requeue/   superadmin — retry a stuck/failed batch
  GET  /api/admin/ops/outbox/                 Telegram notifications
  POST /api/admin/ops/outbox/{id}/retry/      re-send a failed notification
  GET  /api/admin/ops/audit/                  the full admin audit trail
  GET  /api/admin/ops/posts/                  posts, for moderation
  POST /api/admin/ops/posts/{id}/cancel/      superadmin — cancel + refund a post

Why these exist: a `failed` verification batch is never retried by the
stuck-batch sweeper (it only sweeps pending/processing), a FAILED notification
older than 24h is never retried either, and the gateway running out of credits
parks every claim as "pending" with nothing on screen. All of that was
invisible without a database console.
"""
import logging
import time
import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select

from app.core.config import settings
from app.core.deps import require_admin, require_superadmin
from app.core.errors import BadRequest, Conflict, NotFound
from app.core.time_utils import utcnow
from app.db.session import get_session
from app.integrations.x_stream import GatewayUnavailable, XStreamClient, get_x_stream_client
from app.models.audit_log import AuditLog
from app.models.engagement import Engagement
from app.models.outbox_event import OutboxEvent
from app.models.post import Post
from app.models.user import User
from app.models.verification_batch import VerificationBatch

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/ops", tags=["admin-ops"])

# a batch waiting longer than this is "held", not just queued: the X gateway
# refusing our key parks claims here with "we'll retry automatically"
HELD_AFTER = timedelta(minutes=15)
_GATEWAY_CACHE_S = 60
_gateway_cache: dict = {"at": 0.0, "value": None}


def _iso(dt):
    # naive UTC in the DB; say so, so the browser doesn't read it as local time
    return dt.isoformat() + "Z" if dt else None


def _handle(user: User | None) -> str | None:
    if user is None:
        return None
    return user.telegram_username or user.x_username or user.display_name or (
        str(user.telegram_id) if user.telegram_id else None
    )


def _audit(db, *, admin_id, action, target_type, target_id, detail=None):
    db.add(AuditLog(
        actor_id=admin_id, action=action, target_type=target_type,
        target_id=target_id, detail=detail or {},
    ))


# ---- health ----
async def _gateway_health(client: XStreamClient) -> dict:
    now = time.monotonic()
    if _gateway_cache["value"] is not None and now - _gateway_cache["at"] < _GATEWAY_CACHE_S:
        return _gateway_cache["value"]
    if not client.configured:
        value = {"configured": False, "reachable": False, "credits": None, "error": "LOUDRR_GATEWAY_API is not set"}
    else:
        try:
            info = await client.account_info()
            value = {"configured": True, "reachable": True, "credits": info.get("recharge_credits"), "error": None}
        except GatewayUnavailable as e:
            value = {"configured": True, "reachable": False, "credits": None, "error": str(e)[:200]}
    value["checked_at"] = _iso(utcnow())
    _gateway_cache.update(at=now, value=value)
    return value


async def _last_sponsor_poll() -> str | None:
    """The sponsor listener's watermark in Redis (services/sponsor_stream.py)."""
    if not settings.redis_url:
        return None
    try:
        import redis.asyncio as aioredis

        from app.services.sponsor_stream import WATERMARK_KEY

        r = aioredis.from_url(settings.redis_url)
        try:
            raw = await r.get(WATERMARK_KEY)
        finally:
            await r.aclose()
        if raw is None:
            return None
        value = raw.decode() if isinstance(raw, bytes) else str(raw)
        return value if value.endswith("Z") else value + "Z"
    except Exception as e:  # health must never 500 because Redis blinked
        logger.warning("ops health: couldn't read the sponsor watermark: %r", e)
        return None


@router.get("/health/")
async def ops_health(
    _admin: User = Depends(require_admin),
    db=Depends(get_session),
    client: XStreamClient = Depends(get_x_stream_client),
):
    now = utcnow()
    batch_rows = (
        await db.execute(
            select(VerificationBatch.status, func.count(), func.min(VerificationBatch.created_at))
            .group_by(VerificationBatch.status)
        )
    ).all()
    by_status = {s: (n, oldest) for s, n, oldest in batch_rows}
    held = await db.scalar(
        select(func.count()).select_from(VerificationBatch).where(
            VerificationBatch.status.in_(("pending", "processing")),
            VerificationBatch.created_at < now - HELD_AFTER,
        )
    )
    failed_24h = await db.scalar(
        select(func.count()).select_from(VerificationBatch).where(
            VerificationBatch.status == "failed", VerificationBatch.created_at > now - timedelta(hours=24),
        )
    )
    oldest_waiting = min(
        [v[1] for k, v in by_status.items() if k in ("pending", "processing") and v[1]], default=None,
    )
    outbox_rows = dict(
        (await db.execute(select(OutboxEvent.status, func.count()).group_by(OutboxEvent.status))).all()
    )
    from app.models.sponsored_account import SponsoredAccount
    active_sponsors = await db.scalar(
        select(func.count()).select_from(SponsoredAccount).where(SponsoredAccount.is_active.is_(True))
    )
    last_poll = await _last_sponsor_poll()
    minutes_since_poll = None
    if last_poll:
        try:
            polled = datetime.fromisoformat(last_poll.rstrip("Z"))
            minutes_since_poll = max(0, int((now - polled).total_seconds() // 60))
        except ValueError:
            pass
    return {
        "gateway": await _gateway_health(client),
        "sponsor_feed": {
            "stream_enabled": settings.sponsor_stream_enabled,
            "active_sponsors": int(active_sponsors or 0),
            "last_poll_at": last_poll,
            "minutes_since_poll": minutes_since_poll,
            "poll_seconds": settings.sponsor_poll_seconds,
        },
        "batches": {
            "pending": int(by_status.get("pending", (0, None))[0]),
            "processing": int(by_status.get("processing", (0, None))[0]),
            "failed": int(by_status.get("failed", (0, None))[0]),
            "held": int(held or 0),
            "failed_24h": int(failed_24h or 0),
            "oldest_waiting_minutes": (
                int((now - oldest_waiting).total_seconds() // 60) if oldest_waiting else None
            ),
        },
        "outbox": {
            "pending": int(outbox_rows.get("pending", 0)),
            "processing": int(outbox_rows.get("processing", 0)),
            "failed": int(outbox_rows.get("failed", 0)),
        },
    }


# ---- claim batches ----
@router.get("/batches/")
async def list_batches(
    status: str = Query(default="", pattern="^(|pending|processing|completed|failed|held)$"),
    limit: int = Query(default=25, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _admin: User = Depends(require_admin),
    db=Depends(get_session),
):
    now = utcnow()
    where = []
    if status == "held":
        where = [VerificationBatch.status.in_(("pending", "processing")),
                 VerificationBatch.created_at < now - HELD_AFTER]
    elif status:
        where = [VerificationBatch.status == status]
    total = await db.scalar(select(func.count()).select_from(VerificationBatch).where(*where))
    rows = (
        await db.execute(
            select(VerificationBatch, User)
            .join(User, User.id == VerificationBatch.user_id)
            .where(*where)
            .order_by(VerificationBatch.created_at.desc(), VerificationBatch.id)
            .limit(limit).offset(offset)
        )
    ).all()
    return {
        "items": [
            {
                "id": str(b.id),
                "user_id": str(b.user_id),
                "user_handle": _handle(u),
                "status": b.status,
                "held": b.status in ("pending", "processing") and b.created_at < now - HELD_AFTER,
                "engagements": len(b.engagement_ids or []),
                "passed": b.passed,
                "failed": b.failed,
                "credits_awarded": float(b.credits_awarded) if b.credits_awarded is not None else None,
                "message": b.message or "",
                "age_minutes": int((now - b.created_at).total_seconds() // 60),
                "created_at": _iso(b.created_at),
                "completed_at": _iso(b.completed_at),
            }
            for b, u in rows
        ],
        "total": int(total or 0), "limit": limit, "offset": offset,
    }


@router.post("/batches/{batch_id}/requeue/")
async def requeue_batch(
    batch_id: uuid.UUID,
    admin: User = Depends(require_superadmin),
    db=Depends(get_session),
):
    """Send a stuck or FAILED batch back through verification. The sweeper
    never retries `failed`, so without this those engagements stay unpaid."""
    batch = await db.get(VerificationBatch, batch_id)
    if batch is None:
        raise NotFound("Batch not found")
    if batch.status == "completed":
        raise Conflict("That batch already completed")
    previous = batch.status
    batch.status = "pending"
    batch.completed_at = None
    batch.message = "Requeued by an admin"
    _audit(db, admin_id=admin.id, action="requeue_batch", target_type="verification_batch",
           target_id=batch.id, detail={"previous_status": previous})
    await db.commit()
    from app.tasks.enqueue import enqueue

    queued = await enqueue("process_verification_batch", str(batch.id), job_id=f"verify:{batch.id}")
    return {"ok": True, "batch_id": str(batch.id), "status": batch.status, "queued": bool(queued)}


# ---- notifications ----
@router.get("/outbox/")
async def list_outbox(
    status: str = Query(default="failed", pattern="^(|pending|processing|sent|failed)$"),
    limit: int = Query(default=25, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _admin: User = Depends(require_admin),
    db=Depends(get_session),
):
    where = [OutboxEvent.status == status] if status else []
    total = await db.scalar(select(func.count()).select_from(OutboxEvent).where(*where))
    rows = (
        await db.execute(
            select(OutboxEvent).where(*where)
            .order_by(OutboxEvent.created_at.desc(), OutboxEvent.id).limit(limit).offset(offset)
        )
    ).scalars().all()
    return {
        "items": [
            {
                "id": str(e.id),
                "event_type": e.event_type,
                "status": e.status,
                "retry_count": e.retry_count,
                "max_retries": e.max_retries,
                "error_message": (e.error_message or "")[:500],
                "telegram_id": (e.payload or {}).get("telegram_id"),
                "payload": e.payload or {},
                "created_at": _iso(e.created_at),
                "next_retry_at": _iso(e.next_retry_at),
            }
            for e in rows
        ],
        "total": int(total or 0), "limit": limit, "offset": offset,
    }


@router.post("/outbox/{event_id}/retry/")
async def retry_outbox_event(
    event_id: uuid.UUID,
    admin: User = Depends(require_admin),
    db=Depends(get_session),
):
    """Put a FAILED notification back in the queue now. The hourly retry job
    only revives failures from the last 24h, so older ones are dead otherwise."""
    event = await db.get(OutboxEvent, event_id)
    if event is None:
        raise NotFound("Notification not found")
    if event.status != "failed":
        raise Conflict(f"Only failed notifications can be retried (this one is {event.status})")
    event.status = "pending"
    event.retry_count = 0
    event.next_retry_at = None
    _audit(db, admin_id=admin.id, action="retry_notification", target_type="outbox_event",
           target_id=event.id, detail={"event_type": event.event_type})
    await db.commit()
    return {"ok": True, "event_id": str(event.id), "status": event.status}


# ---- audit log ----
@router.get("/audit/")
async def list_audit(
    action: str = Query(default="", max_length=50),
    actor: str = Query(default="", max_length=64),
    target_id: str = Query(default="", max_length=64),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _admin: User = Depends(require_admin),
    db=Depends(get_session),
):
    where = []
    if action:
        where.append(AuditLog.action == action)
    if target_id:
        try:
            where.append(AuditLog.target_id == uuid.UUID(target_id))
        except ValueError:
            raise BadRequest("target_id must be a UUID")
    actor_user = User.__table__.alias("actor")
    if actor:
        needle = actor.lstrip("@").lower()
        where.append(or_(
            func.lower(actor_user.c.telegram_username) == needle,
            func.lower(actor_user.c.x_username) == needle,
        ))
    base = select(AuditLog, actor_user.c.telegram_username, actor_user.c.x_username, actor_user.c.role).outerjoin(
        actor_user, actor_user.c.id == AuditLog.actor_id,
    ).where(*where)
    total = await db.scalar(select(func.count()).select_from(base.subquery()))
    rows = (
        await db.execute(base.order_by(AuditLog.created_at.desc(), AuditLog.id).limit(limit).offset(offset))
    ).all()
    actions = (
        await db.execute(select(AuditLog.action).distinct().order_by(AuditLog.action))
    ).scalars().all()
    return {
        "items": [
            {
                "id": str(a.id),
                "action": a.action,
                "target_type": a.target_type,
                "target_id": str(a.target_id) if a.target_id else None,
                "detail": a.detail or {},
                "actor_id": str(a.actor_id) if a.actor_id else None,
                "actor_handle": tg or xh,
                "actor_role": role,
                "created_at": _iso(a.created_at),
            }
            for a, tg, xh, role in rows
        ],
        "total": int(total or 0), "limit": limit, "offset": offset,
        "actions": list(actions),
    }


# ---- post moderation ----
@router.get("/posts/")
async def list_posts(
    status: str = Query(default="active", pattern="^(|active|completed|cancelled)$"),
    q: str = Query(default="", max_length=100),
    limit: int = Query(default=25, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _admin: User = Depends(require_admin),
    db=Depends(get_session),
):
    where = [Post.status == status] if status else []
    needle = q.strip().lstrip("@").lower()
    if needle:
        escaped = needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        where.append(or_(
            func.lower(Post.tweet_author_username).like(pattern, escape="\\"),
            func.lower(Post.tweet_text).like(pattern, escape="\\"),
            func.lower(Post.x_link).like(pattern, escape="\\"),
        ))
    total = await db.scalar(select(func.count()).select_from(Post).where(*where))
    engagements = (
        select(func.count()).select_from(Engagement).where(Engagement.post_id == Post.id).scalar_subquery()
    )
    rows = (
        await db.execute(
            select(Post, User, engagements.label("engagements"))
            .join(User, User.id == Post.user_id)
            .where(*where)
            .order_by(Post.created_at.desc(), Post.id)
            .limit(limit).offset(offset)
        )
    ).all()
    return {
        "items": [
            {
                "id": str(p.id),
                "x_link": p.x_link,
                "tweet_text": (p.tweet_text or "")[:280],
                "author": p.tweet_author_username or None,
                "poster_id": str(p.user_id),
                "poster_handle": _handle(u),
                "is_sponsored": p.is_sponsored,
                "status": p.status,
                "escrow": float(p.escrow),
                "initial_escrow": float(p.initial_escrow),
                "engagements": int(n or 0),
                "created_at": _iso(p.created_at),
            }
            for p, u, n in rows
        ],
        "total": int(total or 0), "limit": limit, "offset": offset,
    }


class CancelPostBody(BaseModel):
    reason: str = Field(default="", max_length=500)


@router.post("/posts/{post_id}/cancel/")
async def cancel_post(
    post_id: uuid.UUID,
    body: CancelPostBody,
    admin: User = Depends(require_superadmin),
    db=Depends(get_session),
):
    """Pull a post from Engage now (scam link, deleted tweet, abuse). The
    poster gets the unspent escrow back; a sponsored post's platform-funded
    escrow just lapses, like on expiry."""
    from app.services import posts as posts_svc

    post = await db.get(Post, post_id)
    if post is None:
        raise NotFound("Post not found")
    if post.status != "active":
        raise Conflict(f"Post is already {post.status}")
    refund_amount = float(post.escrow)
    refunded = post.platform != "sponsor"
    _audit(db, admin_id=admin.id, action="cancel_post", target_type="post", target_id=post.id,
           detail={"reason": body.reason, "refunded": refund_amount if refunded else 0,
                   "x_link": post.x_link})
    await posts_svc.cancel_post(db, post, refund=refunded)   # commits (audit row included)
    return {"ok": True, "post_id": str(post.id), "status": post.status,
            "refunded": refund_amount if refunded else 0.0}
