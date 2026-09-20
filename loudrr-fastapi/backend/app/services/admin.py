"""Admin operations console (Ch17) — privileged actions, each audit-logged.

These are called by the SQLAdmin panel / admin endpoints (admin auth is separate
from user HMAC auth). Per-feature admin actions built earlier — approve/reject a
waitlist entry (Ch8), approve an X-verification (Ch11) — are joined here by the
credit and ban operations, and every one writes an immutable audit_logs row.
"""
import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select

from app.core.errors import BadRequest, Conflict
from app.core.time_utils import utcnow
from app.models.audit_log import AuditLog
from app.models.user import User
from app.models.x_verification_request import XVerificationRequest
from app.repositories.user import UserRepository
from app.services.credits import CreditService
from app.services.sponsors import PLATFORM_USER_ID


@dataclass(frozen=True)
class CreditOpResult:
    """What a grant/revoke ACTUALLY did.

    `requested` is what the admin typed; `applied` is what moved. They differ
    when apply_penalty clamps to the balance, or when an idempotent retry of
    the same request_id lands a second time and changes nothing. The endpoint
    reports `applied` so the toast and the audit row can't lie.
    """
    user: User
    requested: Decimal
    applied: Decimal
    duplicate: bool  # the idempotency key had already been used


async def _audit(db, *, actor_id, action, target_type="", target_id=None, detail=None):
    db.add(AuditLog(
        actor_id=actor_id, action=action, target_type=target_type,
        target_id=target_id, detail=detail or {},
    ))


async def _target_user(db, user_id) -> User:
    """The user a credit/ban op acts on — never the platform account that owns
    sponsored posts (no Telegram to notify, and its balance must stay 0)."""
    if str(user_id) == str(PLATFORM_USER_ID):
        raise BadRequest("That's the platform account for sponsored posts")
    return await UserRepository(db).get_or_404(id=user_id, label="user")


def _reject_self(admin_id, user_id, what: str) -> None:
    """An admin locking themselves out (or draining their own balance) by a
    mis-click is never the intent — refuse it server-side, not just in the UI."""
    if str(admin_id) == str(user_id):
        raise BadRequest(f"You cannot {what} your own account")


async def grant_credits(
    db, *, admin_id, user_id, amount, description="", request_id: str = "",
) -> CreditOpResult:
    """Grant karma. `request_id` is the client-supplied idempotency token: the
    same token replayed (double-click, retried fetch) grants once. Without one
    every call was unique, which defeated CreditService's own duplicate check.
    """
    user = await _target_user(db, user_id)
    requested = Decimal(str(amount))
    before = Decimal(user.credits)
    key = f"grant_{request_id}" if request_id else f"grant_{uuid.uuid4().hex}"
    await CreditService(db, user).admin_grant(
        requested, admin_id=admin_id,
        idempotency_key=key, description=description or "admin grant",
    )
    applied = Decimal(user.credits) - before
    duplicate = applied == 0 and requested > 0
    await _audit(
        db, actor_id=admin_id, action="grant_credits", target_type="user",
        target_id=user_id,
        # `amount` kept for backwards compatibility with rows written before
        # requested/granted existed; the admin's typed note is recorded too —
        # the field is labelled "Description (audit log)", so it must land here.
        detail={
            "amount": str(requested), "requested": str(requested),
            "granted": str(applied), "description": description or "",
            "duplicate": duplicate, "request_id": request_id or "",
        },
    )
    if not duplicate:
        # queue the user-facing notification in THIS transaction
        from app.services.outbox import OutboxService
        await OutboxService.queue_admin_grant_credits(
            db, user_id=user.id, telegram_id=user.telegram_id,
            amount=applied, description=description or "",
        )
    await db.commit()
    return CreditOpResult(user=user, requested=requested, applied=applied, duplicate=duplicate)


async def revoke_credits(
    db, *, admin_id, user_id, amount, reason="", request_id: str = "",
) -> CreditOpResult:
    """Revoke karma. apply_penalty clamps to the balance and writes nothing at
    all when it's already 0, so the deducted amount is measured, not assumed —
    "Revoked 400" must not appear when 355.35 was taken."""
    _reject_self(admin_id, user_id, "revoke karma from")
    user = await _target_user(db, user_id)
    requested = Decimal(str(amount))
    before = Decimal(user.credits)
    key = f"revoke_{request_id}" if request_id else f"revoke_{uuid.uuid4().hex}"
    await CreditService(db, user).apply_penalty(
        requested, admin_id=admin_id,
        idempotency_key=key, description=reason or "admin revoke",
    )
    deducted = before - Decimal(user.credits)
    duplicate = deducted == 0 and before > 0
    await _audit(
        db, actor_id=admin_id, action="revoke_credits", target_type="user",
        target_id=user_id,
        detail={
            "amount": str(requested), "requested": str(requested),
            "deducted": str(deducted), "reason": reason,
            "duplicate": duplicate, "request_id": request_id or "",
        },
    )
    if deducted > 0:
        from app.services.outbox import OutboxService
        await OutboxService.queue_admin_revoke_credits(
            db, user_id=user.id, telegram_id=user.telegram_id,
            amount=deducted, reason=reason or "",
        )
    await db.commit()
    return CreditOpResult(user=user, requested=requested, applied=deducted, duplicate=duplicate)


async def ban_user(db, *, admin_id, user_id, reason="") -> User:
    _reject_self(admin_id, user_id, "ban")
    user = await _target_user(db, user_id)
    was_whitelisted = bool(user.is_whitelisted)
    user.is_banned = True
    user.is_whitelisted = False  # respect the NOT(banned AND whitelisted) constraint
    # record what the ban destroyed so unban can put it back — whitelist is
    # otherwise only ever set by waitlist approval, so without this an unbanned
    # user is locked out of onboarding forever
    await _audit(db, actor_id=admin_id, action="ban_user", target_type="user",
                 target_id=user_id,
                 detail={"reason": reason, "was_whitelisted": was_whitelisted})
    from app.services.outbox import OutboxService
    await OutboxService.queue_admin_ban(
        db, user_id=user.id, telegram_id=user.telegram_id, reason=reason or "",
    )
    await db.commit()
    return user


async def _whitelist_before_last_ban(db, user_id) -> bool:
    """Whether this user was whitelisted at the moment of their most recent ban.

    False for bans written before the flag was recorded — we can't invent
    history, so those keep the old (lossy) behaviour and an admin can flip the
    flag explicitly with set_whitelist.
    """
    row = (
        await db.execute(
            select(AuditLog)
            .where(AuditLog.action == "ban_user", AuditLog.target_id == user_id)
            .order_by(AuditLog.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        return False
    return bool((row.detail or {}).get("was_whitelisted", False))


async def unban_user(db, *, admin_id, user_id) -> User:
    user = await _target_user(db, user_id)
    restored = await _whitelist_before_last_ban(db, user_id)
    user.is_banned = False
    if restored:
        user.is_whitelisted = True
    await _audit(db, actor_id=admin_id, action="unban_user", target_type="user",
                 target_id=user_id, detail={"whitelist_restored": restored})
    await db.commit()
    return user


async def set_whitelist(db, *, admin_id, user_id, value: bool) -> User:
    """Explicitly set is_whitelisted. The only way, other than a waitlist
    approval, to give a user access — needed for anyone whose whitelist a
    pre-fix ban wiped, and for manual grants of access."""
    user = await _target_user(db, user_id)
    if value and user.is_banned:
        # the DB's ban_xor_whitelist check would refuse this write anyway;
        # fail with a sentence instead of an IntegrityError
        raise Conflict("Unban the user first — a banned user cannot be whitelisted")
    old = bool(user.is_whitelisted)
    user.is_whitelisted = bool(value)
    await _audit(db, actor_id=admin_id, action="set_whitelist", target_type="user",
                 target_id=user_id, detail={"old_value": old, "new_value": bool(value)})
    await db.commit()
    return user


async def reject_x_verification(
    db, *, admin_id, request_id, reason="", internal_note="", notes=None,
) -> XVerificationRequest:
    """Reject a pending X-verification: mark it rejected and demote the user
    back to unverified (clearing any pending claim). The fuller drop-back-to-
    waitlist flow is a deeper admin op left for later.

    TWO free-text fields, because they have different audiences:

      * ``reason`` is PUBLIC — it is rendered into the user's Telegram DM
        ("…was rejected. … Note: X"). Write it for them. Empty is fine; the
        message then just ends after the two handles.
      * ``internal_note`` is for the team. It is stored on
        `admin_notes` (an admin-only column, which is where the next reviewer
        reads it as prior-request history) and in `audit_logs`, and never goes
        near the outbox.

    Before the split there was a single ``notes`` field, the admin UI labelled
    it "internal — stored in audit_logs; the user doesn't see it", and it was
    DM'd verbatim. ``notes`` is still accepted for the SQLAdmin panel and older
    callers, and is treated as the INTERNAL note — the safe reading, since that
    is what everyone who wrote one believed they were writing.
    """
    if notes is not None and not internal_note:
        internal_note = notes
    req = (
        await db.execute(
            select(XVerificationRequest).where(XVerificationRequest.id == request_id)
        )
    ).scalar_one_or_none()
    if req is None:
        from app.core.errors import NotFound
        raise NotFound("x-verification request not found")
    if req.status != "PENDING":
        raise Conflict(f"Request is {req.status!r}, cannot reject")

    req.status = "REJECTED"
    req.reviewed_by_id = admin_id
    req.reviewed_at = utcnow()
    # admin-only column: the next reviewer reads it as this user's history
    req.admin_notes = internal_note or ""

    user = await db.get(User, req.user_id)
    if user is not None:
        user.x_verified = False
        user.pending_claimed_x_username = ""
        user.pending_claimed_x_user_id = ""

    await _audit(db, actor_id=admin_id, action="reject_x_verification",
                 target_type="x_verification_request", target_id=request_id,
                 detail={"reason": reason, "internal_note": internal_note or ""})
    if user is not None and user.telegram_id is not None:
        from app.services.outbox import OutboxService
        # PUBLIC reason only — the internal note stops at admin_notes/audit
        await OutboxService.queue_x_verification_rejected(
            db, request_id=req.id, telegram_id=user.telegram_id,
            submitted_x_username=req.submitted_x_username or "",
            claimed_x_username=req.claimed_x_username or "",
            reason=reason or "",
        )
    await db.commit()
    return req
