"""Admin operations console (Ch17) — privileged actions, each audit-logged.

These are called by the SQLAdmin panel / admin endpoints (admin auth is separate
from user HMAC auth). Per-feature admin actions built earlier — approve/reject a
waitlist entry (Ch8), approve an X-verification (Ch11) — are joined here by the
credit and ban operations, and every one writes an immutable audit_logs row.
"""
import uuid
from decimal import Decimal

from sqlalchemy import select

from app.core.errors import Conflict
from app.core.time_utils import utcnow
from app.models.audit_log import AuditLog
from app.models.user import User
from app.models.x_verification_request import XVerificationRequest
from app.repositories.user import UserRepository
from app.services.credits import CreditService


async def _audit(db, *, actor_id, action, target_type="", target_id=None, detail=None):
    db.add(AuditLog(
        actor_id=actor_id, action=action, target_type=target_type,
        target_id=target_id, detail=detail or {},
    ))


async def grant_credits(db, *, admin_id, user_id, amount, description="") -> User:
    user = await UserRepository(db).get_or_404(id=user_id, label="user")
    await CreditService(db, user).admin_grant(
        Decimal(str(amount)), admin_id=admin_id,
        idempotency_key=f"grant_{uuid.uuid4().hex}", description=description or "admin grant",
    )
    await _audit(db, actor_id=admin_id, action="grant_credits", target_type="user",
                 target_id=user_id, detail={"amount": str(amount)})
    # queue the user-facing notification in THIS transaction
    from app.services.outbox import OutboxService
    await OutboxService.queue_admin_grant_credits(
        db, user_id=user.id, telegram_id=user.telegram_id,
        amount=Decimal(str(amount)), description=description or "",
    )
    await db.commit()
    return user


async def revoke_credits(db, *, admin_id, user_id, amount, reason="") -> User:
    user = await UserRepository(db).get_or_404(id=user_id, label="user")
    await CreditService(db, user).apply_penalty(
        Decimal(str(amount)), admin_id=admin_id,
        idempotency_key=f"revoke_{uuid.uuid4().hex}", description=reason or "admin revoke",
    )
    await _audit(db, actor_id=admin_id, action="revoke_credits", target_type="user",
                 target_id=user_id, detail={"amount": str(amount), "reason": reason})
    from app.services.outbox import OutboxService
    await OutboxService.queue_admin_revoke_credits(
        db, user_id=user.id, telegram_id=user.telegram_id,
        amount=Decimal(str(amount)), reason=reason or "",
    )
    await db.commit()
    return user


async def ban_user(db, *, admin_id, user_id, reason="") -> User:
    user = await UserRepository(db).get_or_404(id=user_id, label="user")
    user.is_banned = True
    user.is_whitelisted = False  # respect the NOT(banned AND whitelisted) constraint
    await _audit(db, actor_id=admin_id, action="ban_user", target_type="user",
                 target_id=user_id, detail={"reason": reason})
    from app.services.outbox import OutboxService
    await OutboxService.queue_admin_ban(
        db, user_id=user.id, telegram_id=user.telegram_id, reason=reason or "",
    )
    await db.commit()
    return user


async def unban_user(db, *, admin_id, user_id) -> User:
    user = await UserRepository(db).get_or_404(id=user_id, label="user")
    user.is_banned = False
    await _audit(db, actor_id=admin_id, action="unban_user", target_type="user",
                 target_id=user_id)
    await db.commit()
    return user


async def reject_x_verification(db, *, admin_id, request_id, notes="") -> XVerificationRequest:
    """Reject a pending X-verification: mark it rejected and demote the user
    back to unverified (clearing any pending claim). The fuller drop-back-to-
    waitlist flow is a deeper admin op left for later."""
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
    req.admin_notes = notes

    user = await db.get(User, req.user_id)
    if user is not None:
        user.x_verified = False
        user.pending_claimed_x_username = ""
        user.pending_claimed_x_user_id = ""

    await _audit(db, actor_id=admin_id, action="reject_x_verification",
                 target_type="x_verification_request", target_id=request_id,
                 detail={"notes": notes})
    if user is not None and user.telegram_id is not None:
        from app.services.outbox import OutboxService
        await OutboxService.queue_x_verification_rejected(
            db, request_id=req.id, telegram_id=user.telegram_id,
            submitted_x_username=req.submitted_x_username or "",
            claimed_x_username=req.claimed_x_username or "",
            notes=notes or "",
        )
    await db.commit()
    return req
