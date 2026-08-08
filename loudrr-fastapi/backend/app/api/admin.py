"""Admin operations API (Ch17) — role-gated, service-backed.

Every privileged action goes through the audited service layer
(services/admin.py, services/waitlist.py, services/x_verification.py) — these
endpoints never touch the DB directly. Access is gated by role:
  * require_admin       — 'admin' or 'superadmin'
  * require_superadmin  — 'superadmin' only (the most sensitive ops, e.g. revoke)

The acting admin's own id is threaded through as the audit actor_id.
Mounted under /api/admin (SQLAdmin owns /admin).
"""
import enum
import uuid
from datetime import timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import or_, select, func

from app.core.deps import require_admin, require_superadmin
from app.core.site_settings_meta import ALL_GROUPS
from app.core.time_utils import utcnow
from app.db.session import get_session
from app.models.audit_log import AuditLog
from app.models.engagement import Engagement
from app.models.post import Post
from app.models.site_setting import SiteSetting
from app.models.transaction import Transaction, TransactionType
from app.models.user import User
from app.models.verification_batch import VerificationBatch
from app.models.waitlist_entry import WaitlistEntry
from app.models.x_verification_request import XVerificationRequest
from app.services import admin as admin_svc
from app.services import site_settings as site_settings_svc
from app.services import waitlist as waitlist_svc
from app.services import x_verification as xverify_svc

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ---- request bodies ----
class GrantBody(BaseModel):
    amount: Decimal = Field(gt=0)
    description: str = ""


class RevokeBody(BaseModel):
    amount: Decimal = Field(gt=0)
    reason: str = ""


class ReasonBody(BaseModel):
    reason: str = ""


class NotesBody(BaseModel):
    notes: str = ""


class SiteSettingUpdateBody(BaseModel):
    value: str = Field(max_length=255)


# ---- identity ----
@router.get("/me/")
async def me(admin: User = Depends(require_admin)):
    """Who am I? Returns the calling admin's identity so the frontend admin
    shell can render the real user (not a hardcoded placeholder) and gate
    the /admin route client-side — non-admins get a 403 here BEFORE the
    admin UI mounts and starts firing data queries that would each 403
    on their own. This endpoint IS the authoritative gate check; server
    RBAC still enforces per-endpoint access, this just lets the UI fail
    fast + friendly."""
    return {
        "id": str(admin.id),
        "telegram_id": admin.telegram_id,
        "telegram_username": admin.telegram_username or "",
        "role": admin.role or "",
    }


# ---- user credit + ban operations ----
@router.post("/users/{user_id}/grant-credits/")
async def grant_credits(
    user_id: uuid.UUID,
    body: GrantBody,
    admin: User = Depends(require_admin),
    db=Depends(get_session),
):
    user = await admin_svc.grant_credits(
        db, admin_id=admin.id, user_id=user_id,
        amount=body.amount, description=body.description,
    )
    return {"ok": True, "user_id": str(user.id), "credits": float(user.credits)}


@router.post("/users/{user_id}/revoke-credits/")
async def revoke_credits(
    user_id: uuid.UUID,
    body: RevokeBody,
    admin: User = Depends(require_superadmin),  # sensitive → superadmin only
    db=Depends(get_session),
):
    user = await admin_svc.revoke_credits(
        db, admin_id=admin.id, user_id=user_id,
        amount=body.amount, reason=body.reason,
    )
    return {"ok": True, "user_id": str(user.id), "credits": float(user.credits)}


@router.post("/users/{user_id}/ban/")
async def ban_user(
    user_id: uuid.UUID,
    body: ReasonBody,
    admin: User = Depends(require_admin),
    db=Depends(get_session),
):
    user = await admin_svc.ban_user(
        db, admin_id=admin.id, user_id=user_id, reason=body.reason
    )
    return {"ok": True, "user_id": str(user.id), "is_banned": user.is_banned}


@router.post("/users/{user_id}/unban/")
async def unban_user(
    user_id: uuid.UUID,
    admin: User = Depends(require_admin),
    db=Depends(get_session),
):
    user = await admin_svc.unban_user(db, admin_id=admin.id, user_id=user_id)
    return {"ok": True, "user_id": str(user.id), "is_banned": user.is_banned}


# ---- waitlist moderation ----
@router.post("/waitlist/{entry_id}/approve/")
async def approve_waitlist(
    entry_id: uuid.UUID,
    admin: User = Depends(require_admin),
    db=Depends(get_session),
):
    created = await waitlist_svc.approve_entry(db, entry_id=entry_id, admin_id=admin.id)
    return {"ok": True, "created_user_id": str(created.id)}


@router.post("/waitlist/{entry_id}/reject/")
async def reject_waitlist(
    entry_id: uuid.UUID,
    body: ReasonBody,
    admin: User = Depends(require_admin),
    db=Depends(get_session),
):
    entry = await waitlist_svc.reject_entry(
        db, entry_id=entry_id, admin_id=admin.id, reason=body.reason
    )
    return {"ok": True, "entry_id": str(entry.id), "status": entry.status}


# ---- X-verification review ----
@router.post("/x-verification/{request_id}/approve/")
async def approve_x_verification(
    request_id: uuid.UUID,
    admin: User = Depends(require_admin),
    db=Depends(get_session),
):
    req = await xverify_svc.approve_x_verification(
        db, request_id=request_id, admin_id=admin.id
    )
    return {"ok": True, "request_id": str(req.id), "status": req.status}


@router.post("/x-verification/{request_id}/reject/")
async def reject_x_verification(
    request_id: uuid.UUID,
    body: NotesBody,
    admin: User = Depends(require_admin),
    db=Depends(get_session),
):
    req = await admin_svc.reject_x_verification(
        db, admin_id=admin.id, request_id=request_id, notes=body.notes
    )
    return {"ok": True, "request_id": str(req.id), "status": req.status}


# ---- read endpoints for the admin UI ----
# Lightweight list/search endpoints so the Next.js admin UI can populate its
# tables without us reusing the SQLAdmin panel for browsing. All admin-gated.
@router.get("/waitlist/pending/")
async def list_pending_waitlist(
    limit: int = Query(default=50, ge=1, le=200),
    _admin: User = Depends(require_admin),
    db=Depends(get_session),
):
    rows = (
        await db.execute(
            select(WaitlistEntry)
            .where(WaitlistEntry.status == "submitted")
            .order_by(WaitlistEntry.created_at.asc())  # oldest first — FIFO
            .limit(limit)
        )
    ).scalars().all()
    return [
        {
            "id": str(e.id),
            "telegram_id": e.telegram_id,
            "telegram_username": e.telegram_username,
            "x_username": e.x_username,
            "region": e.region,
            "niche": e.niche,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in rows
    ]


@router.get("/x-verification/pending/")
async def list_pending_x_verifications(
    limit: int = Query(default=50, ge=1, le=200),
    _admin: User = Depends(require_admin),
    db=Depends(get_session),
):
    rows = (
        await db.execute(
            select(XVerificationRequest, User)
            .join(User, User.id == XVerificationRequest.user_id)
            .where(XVerificationRequest.status == "PENDING")
            .order_by(XVerificationRequest.created_at.asc())
            .limit(limit)
        )
    ).all()
    return [
        {
            "id": str(req.id),
            "user_id": str(req.user_id),
            "user_telegram_username": user.telegram_username,
            "submitted_x_username": req.submitted_x_username,
            "claimed_x_username": req.claimed_x_username,
            "created_at": req.created_at.isoformat() if req.created_at else None,
        }
        for req, user in rows
    ]


@router.get("/users/")
async def search_users(
    q: str = Query(default="", description="Search telegram_username or x_username (case-insensitive substring)"),
    limit: int = Query(default=50, ge=1, le=200),
    _admin: User = Depends(require_admin),
    db=Depends(get_session),
):
    """Search users for the admin Users tab. Empty `q` returns most-recent users."""
    stmt = select(User).order_by(User.created_at.desc()).limit(limit)
    if q:
        needle = f"%{q.lower()}%"
        stmt = (
            select(User)
            .where(
                or_(
                    func.lower(User.telegram_username).like(needle),
                    func.lower(User.x_username).like(needle),
                )
            )
            .order_by(User.created_at.desc())
            .limit(limit)
        )
    rows = (await db.execute(stmt)).scalars().all()
    return [
        {
            "id": str(u.id),
            "telegram_id": u.telegram_id,
            "telegram_username": u.telegram_username,
            "x_username": u.x_username,
            "credits": float(u.credits),
            "role": u.role,
            "is_banned": u.is_banned,
            "is_whitelisted": u.is_whitelisted,
            "x_verified": u.x_verified,
        }
        for u in rows
    ]


# ---- site settings (admin tunables: money math, caps, feature toggles) ----
# Source of truth for *which* keys exist + their docs is ALL_GROUPS in
# core/site_settings_meta.py. The SiteSetting table only stores the *current
# values* — missing rows fall back to the spec default and are flagged
# persisted=false so the UI can show "(default, not yet stored)".
def _coerce_value(value: str, data_type: str):
    """Coerce a raw string per data_type. Raises ValueError if it doesn't fit."""
    if data_type == "int":
        return int(value)
    if data_type == "float":
        return float(value)
    if data_type == "decimal":
        return Decimal(value)
    if data_type == "bool":
        if value.strip().lower() not in {"1", "true", "yes", "on", "0", "false", "no", "off"}:
            raise ValueError(f"{value!r} is not a valid bool")
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if data_type == "str":
        return value
    raise ValueError(f"unknown data_type {data_type!r}")


@router.get("/site-settings/")
async def list_site_settings(
    _admin: User = Depends(require_admin),
    db=Depends(get_session),
):
    """Return every known setting grouped by category. The metadata
    (groups + specs + defaults + data_type) lives in
    core/site_settings_meta.ALL_GROUPS — we just overlay the persisted
    SiteSetting row's value where it exists."""
    # one query to fetch everything currently persisted
    persisted_rows = (await db.execute(select(SiteSetting))).scalars().all()
    by_key = {row.key: row for row in persisted_rows}

    groups_out = []
    for group in ALL_GROUPS:
        settings_out = []
        for spec in group.settings:
            row = by_key.get(spec.key)
            persisted = row is not None
            # Narrow the Optional so mypy doesn't complain about row.value on None.
            value = row.value if row is not None else spec.default
            settings_out.append({
                "key": spec.key,
                "value": value,
                "default": spec.default,
                "data_type": spec.data_type,
                "description": spec.description,
                "live": spec.live,
                "persisted": persisted,
            })
        groups_out.append({
            "name": group.name,
            "description": group.description,
            "settings": settings_out,
        })
    return {"groups": groups_out}


@router.put("/site-settings/{key}")
async def update_site_setting(
    key: str,
    body: SiteSettingUpdateBody,
    admin: User = Depends(require_superadmin),  # tunes money math → superadmin only
    db=Depends(get_session),
):
    """Upsert a single setting. The key must be declared in ALL_GROUPS;
    the value must coerce cleanly to the spec's data_type."""
    # find the spec across all groups
    spec = None
    for group in ALL_GROUPS:
        for s in group.settings:
            if s.key == key:
                spec = s
                break
        if spec is not None:
            break
    if spec is None:
        raise HTTPException(status_code=404, detail=f"unknown setting key {key!r}")

    if len(body.value) > 255:
        raise HTTPException(status_code=422, detail="value too long (max 255 chars)")

    try:
        _coerce_value(body.value, spec.data_type)
    except (ValueError, ArithmeticError) as e:
        raise HTTPException(
            status_code=422,
            detail=f"value does not coerce to {spec.data_type}: {e}",
        )

    existing = (
        await db.execute(select(SiteSetting).where(SiteSetting.key == key))
    ).scalar_one_or_none()
    old_value = existing.value if existing is not None else None

    if existing is None:
        db.add(SiteSetting(
            key=key, value=body.value,
            data_type=spec.data_type, description=spec.description,
        ))
    else:
        existing.value = body.value
        # keep data_type / description in sync with the spec
        existing.data_type = spec.data_type
        existing.description = spec.description

    # bust the in-process cache so the next read sees the new value
    site_settings_svc._cache.pop(key, None)

    db.add(AuditLog(
        actor_id=admin.id,
        action="update_site_setting",
        target_type="site_setting",
        target_id=None,
        detail={"key": key, "old_value": old_value, "new_value": body.value},
    ))
    await db.commit()

    # TIER_* keys feed the in-memory tier bands consulted by sync
    # tier_for/multiplier_for callers; rebuild immediately so the change
    # takes effect without a restart. A failure here is non-fatal —
    # log via the helper's own warning and keep serving the response.
    if key.startswith("TIER_"):
        from app.services.tier import load_tiers_from_settings
        try:
            await load_tiers_from_settings(db)
        except Exception:  # pragma: no cover — defensive
            pass

    return {"ok": True, "key": key, "value": body.value, "data_type": spec.data_type}


# ---- dashboard stats (one round-trip for the admin homepage) ----
@router.get("/stats/")
async def admin_stats(
    _admin: User = Depends(require_admin),
    db=Depends(get_session),
):
    """One-shot dashboard metrics for the admin UI. Everything is computed
    in aggregate SQL (count / sum / group-by) — no N+1, no row iteration."""
    now = utcnow()
    week_ago = now - timedelta(days=7)
    today_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # ---- users (one grouped query + a few small filtered counts) ----
    user_total = (await db.execute(select(func.count(User.id)))).scalar_one()
    role_rows = (
        await db.execute(select(User.role, func.count(User.id)).group_by(User.role))
    ).all()
    by_role = {"regular": 0, "admin": 0, "superadmin": 0}
    for role, cnt in role_rows:
        key = role if role else "regular"
        by_role[key] = by_role.get(key, 0) + int(cnt)

    banned = (
        await db.execute(select(func.count(User.id)).where(User.is_banned.is_(True)))
    ).scalar_one()
    whitelisted = (
        await db.execute(select(func.count(User.id)).where(User.is_whitelisted.is_(True)))
    ).scalar_one()
    x_verified = (
        await db.execute(select(func.count(User.id)).where(User.x_verified.is_(True)))
    ).scalar_one()
    new_users_week = (
        await db.execute(
            select(func.count(User.id)).where(User.created_at >= week_ago)
        )
    ).scalar_one()

    # ---- credits (single aggregate query, three SUMs together) ----
    credit_row = (
        await db.execute(
            select(
                func.coalesce(func.sum(User.credits), 0),
                func.coalesce(func.sum(User.total_credits_earned), 0),
                func.coalesce(func.sum(User.total_credits_spent), 0),
            )
        )
    ).one()
    in_circulation, total_earned, total_spent = credit_row

    # ---- posts (status counts + escrow sum on active) ----
    post_status_rows = (
        await db.execute(select(Post.status, func.count(Post.id)).group_by(Post.status))
    ).all()
    post_counts = {"active": 0, "completed": 0, "cancelled": 0}
    for status, cnt in post_status_rows:
        if status in post_counts:
            post_counts[status] = int(cnt)

    total_escrow_active = (
        await db.execute(
            select(func.coalesce(func.sum(Post.escrow), 0)).where(Post.status == "active")
        )
    ).scalar_one()

    # ---- engagements (total + today + 7d) ----
    eng_total = (await db.execute(select(func.count(Engagement.id)))).scalar_one()
    eng_today = (
        await db.execute(
            select(func.count(Engagement.id)).where(
                Engagement.clicked_at >= today_midnight
            )
        )
    ).scalar_one()
    eng_week = (
        await db.execute(
            select(func.count(Engagement.id)).where(Engagement.clicked_at >= week_ago)
        )
    ).scalar_one()

    # ---- queues (admin moderation backlogs) ----
    pending_waitlist = (
        await db.execute(
            select(func.count(WaitlistEntry.id)).where(
                WaitlistEntry.status == "submitted"
            )
        )
    ).scalar_one()
    pending_x = (
        await db.execute(
            select(func.count(XVerificationRequest.id)).where(
                XVerificationRequest.status == "PENDING"
            )
        )
    ).scalar_one()
    pending_batches = (
        await db.execute(
            select(func.count(VerificationBatch.id)).where(
                VerificationBatch.status.in_(("pending", "processing"))
            )
        )
    ).scalar_one()

    # ---- recent audit (most recent 10 rows) ----
    recent_rows = (
        await db.execute(
            select(AuditLog).order_by(AuditLog.created_at.desc()).limit(10)
        )
    ).scalars().all()
    recent_audit = [
        {
            "id": str(r.id),
            "actor_id": str(r.actor_id) if r.actor_id else None,
            "action": r.action,
            "target_type": r.target_type,
            "target_id": str(r.target_id) if r.target_id else None,
            "detail": r.detail,
            "created_at_iso": r.created_at.isoformat() if r.created_at else None,
        }
        for r in recent_rows
    ]

    return {
        "users": {
            "total": int(user_total),
            "by_role": {k: int(v) for k, v in by_role.items()},
            "banned": int(banned),
            "whitelisted": int(whitelisted),
            "x_verified": int(x_verified),
            "new_this_week": int(new_users_week),
        },
        "credits": {
            "in_circulation": float(in_circulation),
            "total_earned": float(total_earned),
            "total_spent": float(total_spent),
        },
        "posts": {
            "active": post_counts["active"],
            "completed": post_counts["completed"],
            "cancelled": post_counts["cancelled"],
            "total_escrow_active": float(total_escrow_active),
        },
        "engagements": {
            "total": int(eng_total),
            "today": int(eng_today),
            "this_week": int(eng_week),
        },
        "queues": {
            "pending_waitlist": int(pending_waitlist),
            "pending_x_verifications": int(pending_x),
            "pending_batches": int(pending_batches),
        },
        "recent_audit": recent_audit,
    }


# ---- timeseries stats (per-day buckets for the admin charts) ----
class TimeseriesMetric(str, enum.Enum):
    """Which series the admin UI is asking for."""
    KARMA_EARNED = "karma_earned"
    ENGAGEMENTS = "engagements"
    NEW_USERS = "new_users"


async def _bucketed_series(db, *, metric: TimeseriesMetric, start, end):
    """Run ONE aggregate query per metric, bucketed by day via date_trunc.

    Returns a dict {date_obj: numeric_total} for every day the DB actually
    produced a row for. Missing days are filled by the caller.
    """
    if metric is TimeseriesMetric.KARMA_EARNED:
        column = Transaction.created_at
        agg = func.coalesce(func.sum(Transaction.amount), 0)
        stmt = (
            select(func.date_trunc("day", column).label("bucket"), agg.label("value"))
            .where(Transaction.type == TransactionType.EARNED)
            .where(column >= start)
            .where(column < end)
            .group_by("bucket")
        )
    elif metric is TimeseriesMetric.ENGAGEMENTS:
        column = Engagement.clicked_at
        stmt = (
            select(
                func.date_trunc("day", column).label("bucket"),
                func.count(Engagement.id).label("value"),
            )
            .where(column >= start)
            .where(column < end)
            .group_by("bucket")
        )
    elif metric is TimeseriesMetric.NEW_USERS:
        column = User.created_at
        stmt = (
            select(
                func.date_trunc("day", column).label("bucket"),
                func.count(User.id).label("value"),
            )
            .where(column >= start)
            .where(column < end)
            .group_by("bucket")
        )
    else:  # pragma: no cover — enum is exhaustive
        raise HTTPException(status_code=422, detail=f"unknown metric {metric!r}")

    rows = (await db.execute(stmt)).all()
    out = {}
    for bucket, value in rows:
        # bucket is a datetime at midnight; key by date for easy fill
        out[bucket.date()] = float(value) if value is not None else 0.0
    return out


@router.get("/stats/timeseries")
async def stats_timeseries(
    metric: TimeseriesMetric = Query(...),
    days: int = Query(default=30, ge=1, le=90),
    _admin: User = Depends(require_admin),
    db=Depends(get_session),
):
    """Per-day bucketed totals for one metric over the last `days` days.

    The series is contiguous: missing days are filled with 0 so the chart
    always renders `days` points. `delta_pct` compares this window's total
    to the prior equivalent window (e.g. 30d vs the 30d before that) and
    is null when the prior window had no data.
    """
    now = utcnow()
    today_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    # window is [start, end) where end is tomorrow-midnight so today is included
    end = today_midnight + timedelta(days=1)
    start = end - timedelta(days=days)
    prior_end = start
    prior_start = prior_end - timedelta(days=days)

    current_by_day = await _bucketed_series(db, metric=metric, start=start, end=end)
    prior_by_day = await _bucketed_series(db, metric=metric, start=prior_start, end=prior_end)

    # build a contiguous series of `days` points, oldest first
    points = []
    total = 0.0
    for i in range(days):
        d = (start + timedelta(days=i)).date()
        v = current_by_day.get(d, 0.0)
        total += v
        points.append({"date": d.isoformat(), "value": v})

    prior_total = sum(prior_by_day.values())
    if prior_total > 0:
        delta_pct: float | None = round(((total - prior_total) / prior_total) * 100.0, 2)
    else:
        delta_pct = None

    return {
        "metric": metric.value,
        "days": days,
        "points": points,
        "total": total,
        "delta_pct": delta_pct,
    }
