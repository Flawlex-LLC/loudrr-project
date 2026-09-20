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
from sqlalchemy import String, or_, select, func

from app.core import site_settings_meta as settings_meta
from app.core.deps import require_admin, require_superadmin
from app.core.site_settings_meta import ALL_GROUPS
from app.core.time_utils import utcnow
from app.db.session import get_session
from app.integrations.x_stream import XStreamClient, get_x_stream_client
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
from app.services import sponsors as sponsors_svc
from app.services import tier as tier_svc
from app.services import waitlist as waitlist_svc
from app.services import x_verification as xverify_svc

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ---- request bodies ----
class GrantBody(BaseModel):
    # credits is Numeric(12,4) → anything over 99999999.9999 blew up with an
    # opaque 500 (numeric field overflow) instead of a validation error
    amount: Decimal = Field(gt=0, le=Decimal("99999999.9999"))
    description: str = Field(default="", max_length=500)
    # client-supplied idempotency token: the same token replayed (double-click,
    # retried fetch) grants exactly once
    request_id: str = Field(default="", max_length=64)


class RevokeBody(BaseModel):
    amount: Decimal = Field(gt=0, le=Decimal("99999999.9999"))
    reason: str = Field(default="", max_length=500)
    request_id: str = Field(default="", max_length=64)


class WhitelistBody(BaseModel):
    value: bool


class ReasonBody(BaseModel):
    reason: str = ""


class NotesBody(BaseModel):
    notes: str = ""


# The rejection bodies for the two review queues carry TWO fields, and which
# is which matters: `reason` is written for the applicant and is DM'd to them
# verbatim; `internal_note` is written for the team and reaches `audit_logs`
# and nothing else. Until this split there was one field, both admin pages
# labelled it "internal — the applicant doesn't see it", and it went straight
# into their Telegram message.
#
# The caps match the copy they end up in: a DM stays readable at ~300 chars,
# while an internal note can be a paragraph of evidence.
PUBLIC_REASON_MAX = 300
INTERNAL_NOTE_MAX = 1000


class WaitlistRejectBody(BaseModel):
    reason: str = Field(default="", max_length=PUBLIC_REASON_MAX)
    internal_note: str = Field(default="", max_length=INTERNAL_NOTE_MAX)


class XVerificationRejectBody(BaseModel):
    reason: str = Field(default="", max_length=PUBLIC_REASON_MAX)
    internal_note: str = Field(default="", max_length=INTERNAL_NOTE_MAX)


class SiteSettingUpdateBody(BaseModel):
    value: str = Field(max_length=255)


class SponsorCreateBody(BaseModel):
    x_username: str = Field(min_length=1, max_length=120)
    karma_per_post: Decimal = Field(default=sponsors_svc.DEFAULT_KARMA_PER_POST)
    notes: str = Field(default="", max_length=2000)


class SponsorUpdateBody(BaseModel):
    is_active: bool | None = None
    karma_per_post: Decimal | None = None
    notes: str | None = Field(default=None, max_length=2000)


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
    res = await admin_svc.grant_credits(
        db, admin_id=admin.id, user_id=user_id,
        amount=body.amount, description=body.description,
        request_id=body.request_id,
    )
    return {
        "ok": True, "user_id": str(res.user.id), "credits": float(res.user.credits),
        "requested": float(res.requested), "granted": float(res.applied),
        "duplicate": res.duplicate,
    }


@router.post("/users/{user_id}/revoke-credits/")
async def revoke_credits(
    user_id: uuid.UUID,
    body: RevokeBody,
    admin: User = Depends(require_superadmin),  # sensitive → superadmin only
    db=Depends(get_session),
):
    """`deducted` is what actually left the balance — apply_penalty clamps to
    the balance, so a 400 revoke against 355.35 removes 355.35. The UI toasts
    and the audit row both use this number, not the request."""
    res = await admin_svc.revoke_credits(
        db, admin_id=admin.id, user_id=user_id,
        amount=body.amount, reason=body.reason, request_id=body.request_id,
    )
    return {
        "ok": True, "user_id": str(res.user.id), "credits": float(res.user.credits),
        "requested": float(res.requested), "deducted": float(res.applied),
        "clamped": res.applied < res.requested, "duplicate": res.duplicate,
    }


@router.post("/users/{user_id}/whitelist/")
async def set_whitelist(
    user_id: uuid.UUID,
    body: WhitelistBody,
    admin: User = Depends(require_superadmin),  # grants product access → superadmin
    db=Depends(get_session),
):
    """Set is_whitelisted explicitly. Before this, whitelist was only ever set
    by waitlist approval and only ever cleared by a ban — so an unbanned user
    was locked out of onboarding with no way back."""
    user = await admin_svc.set_whitelist(
        db, admin_id=admin.id, user_id=user_id, value=body.value
    )
    return {"ok": True, "user_id": str(user.id), "is_whitelisted": user.is_whitelisted}


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
    return {
        "ok": True, "user_id": str(user.id), "is_banned": user.is_banned,
        "is_whitelisted": user.is_whitelisted,
    }


@router.post("/users/{user_id}/unban/")
async def unban_user(
    user_id: uuid.UUID,
    admin: User = Depends(require_admin),
    db=Depends(get_session),
):
    """Unban also restores the whitelist flag the ban cleared (read back from
    the ban's own audit row), so the user isn't silently left un-onboardable."""
    user = await admin_svc.unban_user(db, admin_id=admin.id, user_id=user_id)
    return {
        "ok": True, "user_id": str(user.id), "is_banned": user.is_banned,
        "is_whitelisted": user.is_whitelisted,
    }


# ---- per-user detail (list → drill-down) ----
# Lives in its own module because it is five read endpoints with their own
# pagination; mounted here so it shares this router's /api/admin prefix and
# main.py needs no change.
from app.api.admin_users import router as admin_users_router  # noqa: E402

router.include_router(admin_users_router)


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
    body: WaitlistRejectBody,
    admin: User = Depends(require_admin),
    db=Depends(get_session),
):
    """`reason` is DM'd to the applicant; `internal_note` only reaches
    `audit_logs`. See WaitlistRejectBody."""
    entry = await waitlist_svc.reject_entry(
        db, entry_id=entry_id, admin_id=admin.id,
        reason=body.reason, internal_note=body.internal_note,
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
    body: XVerificationRejectBody,
    admin: User = Depends(require_admin),
    db=Depends(get_session),
):
    """`reason` is DM'd to the user; `internal_note` is stored on the request
    (`admin_notes`, shown to the next reviewer as history) and audit-logged.
    See XVerificationRejectBody."""
    req = await admin_svc.reject_x_verification(
        db, admin_id=admin.id, request_id=request_id,
        reason=body.reason, internal_note=body.internal_note,
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
            # True for OAuth-first registrations (handle came from X's own
            # /users/me); False only for legacy paste-a-link entries.
            "x_verified": e.x_verified,
            "region": e.region,
            "niche": e.niche,
            "created_at": e.created_at.isoformat() if e.created_at else None,
            # score stored at sign-up (or the applicant's last Refresh) — so
            # approvals aren't blind. null score + null score_updated_at =
            # not fetched yet; null score + a timestamp = provider has none.
            "score": e.score,
            "tier": tier_svc.tier_for(e.score) if e.score is not None else None,
            "smart_followers": (e.score_data or {}).get("smart_followers"),
            "score_updated_at": e.score_updated_at.isoformat() if e.score_updated_at else None,
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


# A search needle is user input going into a LIKE pattern: '%' and '_' are
# LIKE metacharacters, and EVERY seeded handle contains '_', so an un-escaped
# '_' matched every row while a lone '%' returned the whole table. Escape both
# (and the escape char itself) and declare the ESCAPE clause on the operator.
_LIKE_ESCAPE = "\\"


def _like_needle(q: str) -> str:
    esc = (
        q.replace(_LIKE_ESCAPE, _LIKE_ESCAPE * 2)
        .replace("%", _LIKE_ESCAPE + "%")
        .replace("_", _LIKE_ESCAPE + "_")
    )
    return f"%{esc.lower()}%"


def _looks_like_uuid_prefix(q: str) -> bool:
    """A uuid or a uuid prefix pasted out of an audit log — 4+ hex chars with
    optional dashes. Short hex words ('abc') are excluded so a handle search
    doesn't turn into a full-table id scan."""
    stripped = q.replace("-", "")
    return len(stripped) >= 4 and all(c in "0123456789abcdefABCDEF" for c in stripped)


USER_SORTS = {
    "created_at": User.created_at,
    "credits": User.credits,
    "tweetscout_score": User.tweetscout_score,
    "total_engagements": User.total_engagements,
}

USER_FLAGS = ("banned", "not_whitelisted", "admins", "never_scored")


@router.get("/users/")
async def search_users(
    q: str = Query(
        default="",
        description=(
            "Case-insensitive match on telegram_username, x_username, "
            "display_name, referral_code, telegram_id or user id (uuid prefix). "
            "A leading '@' and surrounding whitespace are ignored."
        ),
    ),
    flag: str = Query(default="", description="banned | not_whitelisted | admins | never_scored"),
    sort: str = Query(default="created_at", description="created_at|credits|tweetscout_score|total_engagements"),
    dir: str = Query(default="desc", description="asc|desc"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _admin: User = Depends(require_admin),
    db=Depends(get_session),
):
    """Search + page users for the admin Users tab.

    Returns {rows, total, limit, offset} — `total` is the count of everything
    matching the filters, so the UI can show a real number and paginate
    instead of guessing from a hardcoded limit=100.
    """
    # never the platform account that owns sponsored posts — it isn't a person
    conditions = [User.id != sponsors_svc.PLATFORM_USER_ID]

    needle_raw = (q or "").strip()
    if needle_raw.startswith("@"):  # admins paste "@handle"; the column has no '@'
        needle_raw = needle_raw[1:].strip()
    if needle_raw:
        needle = _like_needle(needle_raw)
        matches = [
            func.lower(User.telegram_username).like(needle, escape=_LIKE_ESCAPE),
            func.lower(User.x_username).like(needle, escape=_LIKE_ESCAPE),
            func.lower(User.display_name).like(needle, escape=_LIKE_ESCAPE),
            func.lower(User.referral_code).like(needle, escape=_LIKE_ESCAPE),
            # the telegram id is PRINTED in the table — it has to be searchable
            func.cast(User.telegram_id, String).like(needle, escape=_LIKE_ESCAPE),
        ]
        if _looks_like_uuid_prefix(needle_raw):
            # a uuid prefix copied out of an audit log / a support ticket
            matches.append(
                func.cast(User.id, String).like(needle, escape=_LIKE_ESCAPE)
            )
        conditions.append(or_(*matches))

    if flag:
        if flag not in USER_FLAGS:
            raise HTTPException(
                status_code=422,
                detail=f"unknown flag {flag!r} (expected one of {', '.join(USER_FLAGS)})",
            )
        if flag == "banned":
            conditions.append(User.is_banned.is_(True))
        elif flag == "not_whitelisted":
            conditions.append(User.is_whitelisted.is_(False))
        elif flag == "admins":
            conditions.append(User.role.in_(("admin", "superadmin")))
        elif flag == "never_scored":
            conditions.append(User.tweetscout_last_updated.is_(None))

    if sort not in USER_SORTS:
        raise HTTPException(
            status_code=422,
            detail=f"unknown sort {sort!r} (expected one of {', '.join(USER_SORTS)})",
        )
    if dir not in ("asc", "desc"):
        raise HTTPException(status_code=422, detail="dir must be 'asc' or 'desc'")

    column = USER_SORTS[sort]
    order = column.asc() if dir == "asc" else column.desc()

    total = (
        await db.execute(select(func.count(User.id)).where(*conditions))
    ).scalar_one()

    rows = (
        await db.execute(
            select(User)
            .where(*conditions)
            # id tiebreak keeps paging stable when the sort column ties
            .order_by(order, User.id.asc())
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()

    return {
        "total": int(total),
        "limit": limit,
        "offset": offset,
        "rows": [
            {
                "id": str(u.id),
                "telegram_id": u.telegram_id,
                "telegram_username": u.telegram_username,
                "x_username": u.x_username,
                "display_name": u.display_name or "",
                "credits": float(u.credits),
                "role": u.role,
                "is_banned": u.is_banned,
                "is_whitelisted": u.is_whitelisted,
                "x_verified": u.x_verified,
                "total_engagements": int(u.total_engagements or 0),
                "created_at": u.created_at.isoformat() if u.created_at else None,
                # score + the tier (karma multiplier) it maps to; score_updated_at
                # is the last refresh attempt (null = never scored)
                "tweetscout_score": float(u.tweetscout_score or 0),
                "tier": tier_svc.tier_for(u.tweetscout_score or 0),
                "score_updated_at": (
                    u.tweetscout_last_updated.isoformat() if u.tweetscout_last_updated else None
                ),
            }
            for u in rows
        ],
    }


# ---- sponsored accounts (their original posts become sponsored raid posts) ----
@router.get("/sponsors/")
async def list_sponsors(
    _admin: User = Depends(require_admin),
    db=Depends(get_session),
):
    return await sponsors_svc.list_sponsors(db)


@router.post("/sponsors/")
async def add_sponsor(
    body: SponsorCreateBody,
    admin: User = Depends(require_admin),
    db=Depends(get_session),
    client: XStreamClient = Depends(get_x_stream_client),
):
    sponsor = await sponsors_svc.add_sponsor(
        db, admin_id=admin.id, handle=body.x_username,
        karma_per_post=body.karma_per_post, notes=body.notes, client=client,
    )
    return await sponsors_svc.sponsor_row(db, sponsor.id)


@router.patch("/sponsors/{sponsor_id}/")
async def update_sponsor(
    sponsor_id: uuid.UUID,
    body: SponsorUpdateBody,
    admin: User = Depends(require_admin),
    db=Depends(get_session),
    client: XStreamClient = Depends(get_x_stream_client),
):
    sponsor = await sponsors_svc.update_sponsor(
        db, admin_id=admin.id, sponsor_id=sponsor_id, is_active=body.is_active,
        karma_per_post=body.karma_per_post, notes=body.notes, client=client,
    )
    return await sponsors_svc.sponsor_row(db, sponsor.id)


@router.delete("/sponsors/{sponsor_id}/")
async def delete_sponsor(
    sponsor_id: uuid.UUID,
    admin: User = Depends(require_admin),
    db=Depends(get_session),
    client: XStreamClient = Depends(get_x_stream_client),
):
    await sponsors_svc.delete_sponsor(db, admin_id=admin.id, sponsor_id=sponsor_id, client=client)
    return {"ok": True}


# ---- site settings (admin tunables: money math, caps, feature toggles) ----
# Source of truth for *which* keys exist + their docs is ALL_GROUPS in
# core/site_settings_meta.py. The SiteSetting table only stores the *current
# values* — missing rows fall back to the spec default and are flagged
# persisted=false so the UI can show "(default, not yet stored)".
def _coerce_value(value: str, data_type: str):
    """Coerce a raw string per data_type. Raises ValueError if it doesn't fit.

    Kept as a thin alias: the real implementation (and the bounds check built
    on it) lives in core/site_settings_meta so seeding, validation and the API
    can never disagree about what a valid value is.
    """
    return settings_meta.coerce_value(value, data_type)


@router.get("/site-settings/")
async def list_site_settings(
    _admin: User = Depends(require_admin),
    db=Depends(get_session),
):
    """Return every known setting grouped by category. The metadata
    (groups + specs + defaults + data_type + bounds) lives in
    core/site_settings_meta.ALL_GROUPS — we just overlay the persisted
    SiteSetting row's value where it exists.

    `min`/`max`/`step`/`unit` are the SAME numbers the PUT enforces, so the
    input attributes and the server rule can't drift apart. `danger` marks a
    setting the UI must confirm before saving, and `impact` is the sentence it
    shows while doing so."""
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
                "min": spec.min,
                "max": spec.max,
                "step": spec.step,
                "unit": spec.unit,
                "danger": spec.danger,
                "impact": spec.impact,
                # true when the current value differs from the shipped default
                # — the UI offers "Reset to default" on exactly these
                "drifted": value != spec.default,
            })
        groups_out.append({
            "name": group.name,
            "description": group.description,
            "settings": settings_out,
        })
    return {
        "groups": groups_out,
        # honest propagation note: the settings cache is per-process with a
        # 300s TTL, and the arq settlement worker is a different process
        "propagation_seconds": site_settings_svc._TTL_SECONDS,
    }


@router.get("/site-settings/{key}/history/")
async def site_setting_history(
    key: str,
    limit: int = Query(default=20, ge=1, le=100),
    _admin: User = Depends(require_admin),
    db=Depends(get_session),
):
    """Every recorded change to one setting, newest first, with the acting
    admin's handle joined in. update_site_setting has always written this audit
    row — nothing surfaced it."""
    if settings_meta.spec_by_key(key) is None:
        raise HTTPException(status_code=404, detail=f"unknown setting key {key!r}")

    actor = User.__table__.alias("actor")
    rows = (
        await db.execute(
            select(AuditLog, actor.c.telegram_username, actor.c.x_username)
            .outerjoin(actor, actor.c.id == AuditLog.actor_id)
            .where(
                AuditLog.action == "update_site_setting",
                AuditLog.detail["key"].astext == key,
            )
            .order_by(AuditLog.created_at.desc())
            .limit(limit)
        )
    ).all()
    return {
        "key": key,
        "rows": [
            {
                "id": str(log.id),
                "old_value": (log.detail or {}).get("old_value"),
                "new_value": (log.detail or {}).get("new_value"),
                "actor_id": str(log.actor_id) if log.actor_id else None,
                "actor_handle": tg or x or "",
                "created_at": log.created_at.isoformat() if log.created_at else None,
            }
            for log, tg, x in rows
        ],
    }


# trailing slash like every other route: the Next.js /api/admin/* rewrite
# always appends one, and a slashless route 307-redirects the admin's browser
# to the backend's INTERNAL origin (unreachable in prod) — saving a setting
# from the dashboard silently failed
@router.put("/site-settings/{key}/")
async def update_site_setting(
    key: str,
    body: SiteSettingUpdateBody,
    admin: User = Depends(require_superadmin),  # tunes money math → superadmin only
    db=Depends(get_session),
):
    """Upsert a single setting. The key must be declared in ALL_GROUPS, the
    value must coerce to the spec's data_type, sit inside the spec's min/max,
    AND leave the cross-field invariants intact (POST_COST_MIN ≤ POST_COST ≤
    POST_COST_MAX, strictly increasing tier thresholds, multipliers ≥ 1).

    Before this, type coercion was the ONLY check: a negative cooldown and a
    15-digit karma cost both saved with a 200."""
    spec = settings_meta.spec_by_key(key)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"unknown setting key {key!r}")

    try:
        settings_meta.validate_value(spec, body.value)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    # cross-field check against the state the DB would be in AFTER this write
    persisted = (await db.execute(select(SiteSetting))).scalars().all()
    resolved = {s.key: s.default for _g, s in settings_meta.all_specs()}
    resolved.update({row.key: row.value for row in persisted})
    resolved[key] = body.value
    try:
        settings_meta.check_invariants(resolved)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    existing = next((row for row in persisted if row.key == key), None)
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

    return {
        "ok": True, "key": key, "value": body.value, "data_type": spec.data_type,
        "old_value": old_value, "default": spec.default,
        "live": spec.live, "danger": spec.danger,
        # this process re-read it the moment we popped the cache; every OTHER
        # process (the other uvicorn workers, the arq settlement worker) keeps
        # its own 300s-TTL copy until it ages out
        "propagation_seconds": site_settings_svc._TTL_SECONDS,
    }


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
    # the platform account that owns sponsored posts isn't a user
    real_users = User.id != sponsors_svc.PLATFORM_USER_ID
    user_total = (await db.execute(select(func.count(User.id)).where(real_users))).scalar_one()
    role_rows = (
        await db.execute(
            select(User.role, func.count(User.id)).where(real_users).group_by(User.role)
        )
    ).all()
    by_role = {"regular": 0, "admin": 0, "superadmin": 0}
    for role, cnt in role_rows:
        key = role if role else "regular"
        by_role[key] = by_role.get(key, 0) + int(cnt)

    banned = (
        await db.execute(select(func.count(User.id)).where(real_users, User.is_banned.is_(True)))
    ).scalar_one()
    whitelisted = (
        await db.execute(
            select(func.count(User.id)).where(real_users, User.is_whitelisted.is_(True))
        )
    ).scalar_one()
    x_verified = (
        await db.execute(select(func.count(User.id)).where(real_users, User.x_verified.is_(True)))
    ).scalar_one()
    new_users_week = (
        await db.execute(
            select(func.count(User.id)).where(real_users, User.created_at >= week_ago)
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
            .where(User.id != sponsors_svc.PLATFORM_USER_ID)  # not a real sign-up
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


# trailing slash: see /site-settings/{key}/ above
@router.get("/stats/timeseries/")
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


# ---- the two review queues ----
# Waitlist + X-verification list/search/paging and the recovery actions
# (reopen a rejection, refresh a missing score) live in their own module —
# they outgrew this file. Included here rather than mounted separately in
# main.py so every path stays under the same /api/admin prefix and the same
# require_admin story.
from app.api.admin_ops import router as _ops_router  # noqa: E402
from app.api.admin_review import router as _review_router  # noqa: E402

router.include_router(_review_router)
router.include_router(_ops_router)
