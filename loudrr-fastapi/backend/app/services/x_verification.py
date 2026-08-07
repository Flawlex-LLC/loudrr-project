"""X verification — the OAuth gate that proves a user owns their X handle (Ch11).

Endpoints 5/6/7 + the public OAuth callback. Flow:
  start  → build an authorize URL, stash PKCE state in the DB
  X redirects the browser to the callback with ?code&state
  callback → consume state, exchange code, read /users/me:
             handle matches  → mark x_verified
             handle differs   → stash a pending claim; the mini-app prompts
  confirm-mismatch → user says "yes that's mine" → open an admin review request
  cancel-mismatch  → user says "no" → clear the prompt, let them retry

External HTTP (token exchange, /users/me) is done holding no DB lock.
"""
import logging
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func

from app.core.db_helpers import locked_row
from app.core.time_utils import utcnow
from app.core.errors import BadRequest, Conflict, Forbidden, ServiceUnavailable
from app.integrations import x_oauth
from app.models.user import User
from app.models.x_oauth_state import XOAuthState
from app.models.x_verification_request import (
    XVerificationRequest,
    XVerificationStatus,
)
from app.repositories.user import UserRepository
from app.repositories.x_verification_request import XVerificationRequestRepository

logger = logging.getLogger(__name__)

_PENDING = XVerificationStatus.PENDING.value


@dataclass
class CallbackResult:
    title: str
    message: str
    success: bool
    status_code: int = 200


# ---- endpoint 5: POST /x-oauth/start/ ----
async def start_oauth(db, *, user: User) -> str:
    if user.is_banned:
        raise Forbidden("Account suspended")
    if user.x_verified:
        raise BadRequest("Already verified")
    if not x_oauth.is_configured():
        raise ServiceUnavailable("X OAuth not configured")

    state = x_oauth.new_state()
    verifier, challenge = x_oauth.make_pkce()
    url = x_oauth.build_authorize_url(state, challenge)
    db.add(
        XOAuthState(
            state=state,
            user_id=user.id,
            code_verifier=verifier,
            expires_at=utcnow()
            + timedelta(seconds=x_oauth.STATE_TTL_SECONDS),
        )
    )
    await db.commit()
    return url


async def _consume_state(db, state: str) -> dict | None:
    """Look up and delete the state row (one-time use). None if missing/expired."""
    row = await db.get(XOAuthState, state)
    if row is None:
        return None
    record = {"user_id": row.user_id, "code_verifier": row.code_verifier}
    expired = row.expires_at < utcnow()
    await db.delete(row)
    await db.commit()
    return None if expired else record


# ---- public OAuth callback: GET /api/auth/x/callback/ ----
async def handle_callback(
    db, *, code: str | None, state: str | None, error: str | None = None
) -> CallbackResult:
    if error:
        return CallbackResult(
            "Authorization Cancelled",
            "You cancelled the connection. Open Loudrr again to retry.",
            False, 400,
        )
    if not code or not state:
        return CallbackResult(
            "Invalid Request",
            "Missing authorization code. Try connecting again from Loudrr.",
            False, 400,
        )
    record = await _consume_state(db, state)
    if record is None:
        return CallbackResult(
            "Session Expired",
            "Your verification link expired. Open Loudrr again to retry.",
            False, 400,
        )
    user = await db.get(User, record["user_id"])
    if user is None:
        return CallbackResult(
            "Account Not Found", "Something went wrong. Open Loudrr again.", False, 404
        )

    token = await x_oauth.exchange_code_for_token(code, record["code_verifier"])
    if not token:
        return CallbackResult(
            "Connection Failed", "Couldn't connect to X. Try again from Loudrr.",
            False, 502,
        )
    me = await x_oauth.fetch_me(token)
    if not me or not me.get("username") or not me.get("id"):
        return CallbackResult(
            "Couldn't Read Profile",
            "X didn't return your profile info. Try again from Loudrr.",
            False, 502,
        )

    claimed_username = me["username"]
    claimed_id = str(me["id"])
    submitted = (user.x_username or "").lstrip("@")

    if submitted and claimed_username.lower() == submitted.lower():
        user.x_username = claimed_username  # canonical case
        user.x_verified = True
        user.x_verified_at = utcnow()
        user.pending_claimed_x_username = ""
        user.pending_claimed_x_user_id = ""
        await db.commit()
        logger.info("[X-OAUTH] user %s verified as @%s", user.id, claimed_username)
        return CallbackResult(
            "Connected!",
            f"@{claimed_username} is verified. Return to Loudrr in Telegram to continue.",
            True, 200,
        )

    # mismatch — stash the claim; the mini-app will prompt to confirm or retry
    user.pending_claimed_x_username = claimed_username
    user.pending_claimed_x_user_id = claimed_id
    await db.commit()
    logger.info(
        "[X-OAUTH] mismatch for user %s: submitted=@%s claimed=@%s",
        user.id, submitted, claimed_username,
    )
    return CallbackResult(
        "Different Account Detected",
        f"You signed up with @{submitted} but logged into @{claimed_username}. "
        "Return to Loudrr in Telegram — we'll ask you what to do next.",
        True, 200,
    )


# ---- endpoint 6: POST /x-verification/confirm-mismatch/ ----
async def confirm_mismatch(db, *, user: User) -> dict:
    claimed_username = user.pending_claimed_x_username
    claimed_id = user.pending_claimed_x_user_id
    if not claimed_username:
        raise BadRequest("No pending mismatch to confirm")

    repo = XVerificationRequestRepository(db)
    # don't open a second review request if one is already pending
    if not await repo.exists(user_id=user.id, status=_PENDING):
        await repo.create(
            user_id=user.id,
            submitted_x_username=user.x_username or "",
            claimed_x_username=claimed_username,
            claimed_x_user_id=claimed_id,
        )
    user.pending_claimed_x_username = ""
    user.pending_claimed_x_user_id = ""
    await db.commit()
    return {"status": "pending_review"}


# ---- endpoint 7: POST /x-verification/cancel-mismatch/ ----
async def cancel_mismatch(db, *, user: User) -> dict:
    user.pending_claimed_x_username = ""
    user.pending_claimed_x_user_id = ""
    await db.commit()
    return {"status": "cleared"}


# ---- read helper for /user/ ----
async def has_pending_review(db, *, user_id) -> bool:
    return await XVerificationRequestRepository(db).exists(
        user_id=user_id, status=_PENDING
    )


# ---- admin review (UI wired in Ch17) ----
async def approve_x_verification(db, *, request_id, admin_id) -> XVerificationRequest:
    """Adopt the claimed handle and mark the user verified. Rejects if the
    handle is already in use by another user (case-insensitive)."""
    repo = XVerificationRequestRepository(db)
    req = await repo.get_or_404(id=request_id, label="verification request")
    if req.status != _PENDING:
        raise Conflict(f"Request is {req.status!r}, cannot approve")

    clash = await UserRepository(db).exists_where(
        func.lower(User.x_username) == req.claimed_x_username.lower(),
        User.id != req.user_id,
    )
    if clash:
        raise Conflict(f"@{req.claimed_x_username} already in use by another user")

    async with locked_row(db, User, id=req.user_id) as user:
        user.x_username = req.claimed_x_username
        user.x_verified = True
        user.x_verified_at = utcnow()
        user.pending_claimed_x_username = ""
        user.pending_claimed_x_user_id = ""
        # capture for the outbox payload — locked_row releases before commit
        telegram_id = user.telegram_id
        canonical_handle = user.x_username

    req.status = XVerificationStatus.APPROVED.value
    req.reviewed_by_id = admin_id
    req.reviewed_at = utcnow()
    # queue the "x_verification_approved" Telegram card in THIS transaction
    if telegram_id is not None:
        from app.services.outbox import OutboxService
        await OutboxService.queue_x_verification_approved(
            db, request_id=req.id, user_id=req.user_id,
            telegram_id=telegram_id, x_username=canonical_handle,
        )
    await db.commit()
    return req

    # TODO Ch17: reject_x_verification — the admin "drop the user back to the
    # waitlist" flow (recreate a submitted WaitlistEntry with the OAuth-verified
    # handle + x_verified_previously=True). Deferred with the admin layer; it
    # also depends on the waitlist_entries.telegram_display_name migration fix.
