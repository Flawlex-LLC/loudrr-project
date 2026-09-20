"""X verification — the OAuth gate that proves a user owns their X handle (Ch11).

Endpoints 5/6/7 + the public OAuth callback. Flow:
  start  → build an authorize URL, stash PKCE state in the DB
  X redirects the browser to the callback with ?code&state
  callback → consume state, exchange code, read /users/me, then STOP: record
             what X proved and show the browser a page naming the X account
             and the Telegram account it would be linked to
  confirm  → (that page's button) apply it:
             handle matches  → mark x_verified
             handle differs   → stash a pending claim; the mini-app prompts
  confirm-mismatch → user says "yes that's mine" → open an admin review request
  cancel-mismatch  → user says "no" → clear the prompt, let them retry

The confirm step exists because the authorize link is forwardable: someone
could start Connect X in their own mini-app, send the link to a victim, and
receive the victim's X identity (and a verified badge, when it matched the
handle they had typed in) the moment the victim authorized.

External HTTP (token exchange, /users/me) is done holding no DB lock.
"""
import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import delete, func, or_, select

from app.core.db_helpers import locked_row
from app.core.time_utils import utcnow
from app.core.errors import BadRequest, Conflict, Forbidden, ServiceUnavailable
from app.integrations import x_oauth
from app.models.user import User
from app.models.x_oauth_confirmation import XOAuthConfirmation
from app.models.x_oauth_state import XOAuthState
from app.models.x_verification_request import (
    XVerificationRequest,
    XVerificationStatus,
)
from app.repositories.user import UserRepository
from app.repositories.x_verification_request import XVerificationRequestRepository

logger = logging.getLogger(__name__)

_PENDING = XVerificationStatus.PENDING.value


# how long the browser has to confirm what it just authorized on X
CONFIRM_TTL_SECONDS = 600


@dataclass
class CallbackResult:
    title: str
    message: str
    success: bool
    status_code: int = 200
    # set when the browser must confirm before anything is applied
    confirm_token: str | None = None
    x_username: str = ""
    telegram_label: str = ""


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _telegram_label(user: User) -> str:
    """Who started the flow, sanitized the same way as the waitlist page."""
    from app.services.waitlist_x_oauth import describe_telegram_user

    return describe_telegram_user({
        "id": user.telegram_id,
        "username": user.telegram_username or "",
        "first_name": user.display_name or "",
    })


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

    # Nothing is applied yet: record what X proved and ask the browser that
    # authorized to confirm which Telegram account gets it. A newer attempt
    # replaces any older pending one for this user.
    token = secrets.token_urlsafe(32)
    label = _telegram_label(user)
    await db.execute(delete(XOAuthConfirmation).where(XOAuthConfirmation.user_id == user.id))
    db.add(XOAuthConfirmation(
        token_hash=_hash_token(token), user_id=user.id,
        x_username=str(me["username"])[:50], x_user_id=str(me["id"])[:50],
        telegram_label=label,
        expires_at=utcnow() + timedelta(seconds=CONFIRM_TTL_SECONDS),
    ))
    await db.commit()
    logger.info("[X-OAUTH] user %s authorized @%s, awaiting browser confirmation", user.id, me["username"])
    return CallbackResult(
        "Confirm this connection",
        "Check the two accounts before you continue.",
        True, 200,
        confirm_token=token, x_username=str(me["username"]), telegram_label=label,
    )


async def decide_confirmation(db, *, token: str, decision: str) -> CallbackResult:
    """The answer given on the callback page. Single use: the row is taken with
    DELETE … RETURNING, so a double submit or a confirm racing a cancel applies
    at most once."""
    gone = CallbackResult(
        "Link Expired",
        "This confirmation link is invalid, already used or expired. "
        "Open Loudrr in Telegram and tap Connect X to start again.",
        False, 404,
    )
    if not token or len(token) > 128 or decision not in ("confirm", "cancel"):
        return gone
    row = (
        await db.execute(
            delete(XOAuthConfirmation)
            .where(
                XOAuthConfirmation.token_hash == _hash_token(token),
                XOAuthConfirmation.expires_at > utcnow(),
            )
            .returning(
                XOAuthConfirmation.user_id,
                XOAuthConfirmation.x_username,
                XOAuthConfirmation.x_user_id,
            )
        )
    ).first()
    await db.commit()
    if row is None:
        return gone
    if decision == "cancel":
        logger.info("[X-OAUTH] user %s: connection to @%s cancelled in browser", row.user_id, row.x_username)
        return CallbackResult(
            "Nothing Was Connected",
            "We cancelled that request. Your X account wasn't linked to anyone.",
            True, 200,
        )
    user = await db.get(User, row.user_id)
    if user is None or user.is_banned:
        return gone
    return await _apply_claim(db, user=user, claimed_username=row.x_username, claimed_id=row.x_user_id)


async def _apply_claim(db, *, user: User, claimed_username: str, claimed_id: str) -> CallbackResult:
    """What the callback used to do on the spot, now only after a confirm."""
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
    # the audit trail carries rejections already; an approval hands someone a
    # verified handle, so it belongs there too
    from app.models.audit_log import AuditLog
    db.add(AuditLog(
        actor_id=admin_id, action="approve_x_verification", target_type="x_verification_request",
        target_id=req.id, detail={"user_id": str(req.user_id), "x_username": canonical_handle},
    ))
    # queue the "x_verification_approved" Telegram card in THIS transaction
    if telegram_id is not None:
        from app.services.outbox import OutboxService
        await OutboxService.queue_x_verification_approved(
            db, request_id=req.id, user_id=req.user_id,
            telegram_id=telegram_id, x_username=canonical_handle,
        )
    await db.commit()
    return req


# Rejection lives in services/admin.reject_x_verification (it demotes the User
# as well as closing the request, which is an admin-console operation rather
# than part of the OAuth flow). The fuller "drop them back to the waitlist"
# variant — recreate a submitted WaitlistEntry with the OAuth-verified handle —
# is still unbuilt; the waitlist side of that undo now exists as
# services/waitlist.reopen_entry.


# ---- THE REVIEW QUEUE (admin panel) ----
# The reviewer's job here is an identity call: is the person who just OAuth'd
# @claimed the same person who signed up as @submitted? The list gave them two
# handles and a date. Everything below is context that was already in the
# database and simply wasn't being read — the user's standing, what the same
# user asked for before and was told, and whether the handle they want is
# already somebody else's.

#: The three states the panel can page through.
REVIEW_STATUSES = ("PENDING", "APPROVED", "REJECTED")

MAX_PAGE_SIZE = 200


def _user_facts(user: User | None) -> dict:
    """The requester's standing — enough to tell a real user from a sock."""
    from app.services import tier as tier_svc

    if user is None:  # FK is ON DELETE CASCADE, so this is defensive only
        return {
            "user_telegram_id": None, "user_telegram_username": "",
            "user_display_name": "", "user_x_username": "",
            "user_score": None, "user_tier": None, "user_credits": 0.0,
            "user_is_banned": False, "user_x_verified": False,
            "user_created_at": None,
        }
    score = float(user.tweetscout_score or 0)
    return {
        "user_telegram_id": user.telegram_id,
        "user_telegram_username": user.telegram_username or "",
        "user_display_name": user.display_name or "",
        # the handle the account currently holds — not necessarily either of
        # the two handles on the request
        "user_x_username": user.x_username or "",
        "user_score": score,
        "user_tier": tier_svc.tier_for(score),
        "user_credits": float(user.credits or 0),
        # a banned impersonator asking to adopt a handle is the exact case
        # this queue exists to catch, and it wasn't on screen
        "user_is_banned": user.is_banned,
        "user_x_verified": user.x_verified,
        "user_created_at": user.created_at.isoformat() if user.created_at else None,
    }


def _request_summary(req: XVerificationRequest) -> dict:
    """A prior request, as shown in the current one's history."""
    return {
        "id": str(req.id),
        "submitted_x_username": req.submitted_x_username or "",
        "claimed_x_username": req.claimed_x_username or "",
        "claimed_x_user_id": req.claimed_x_user_id or "",
        "status": req.status,
        # admin_notes is the reviewer-to-reviewer note. It is NOT sent to the
        # user (that is the public `reason`, which lives in the outbox event
        # and the audit log) — and reading the last reviewer's note is the
        # whole point of showing history: one seeded user is on his third
        # handle after "OAuth'd an old account; asked user to retry".
        "admin_notes": req.admin_notes or "",
        "created_at": req.created_at.isoformat() if req.created_at else None,
        "reviewed_at": req.reviewed_at.isoformat() if req.reviewed_at else None,
    }


async def _prior_requests(db, rows) -> dict:
    """request id -> that user's OTHER requests, newest first. One query."""
    user_ids = {req.user_id for req, _ in rows}
    if not user_ids:
        return {}
    history = (
        await db.execute(
            select(XVerificationRequest)
            .where(XVerificationRequest.user_id.in_(user_ids))
            .order_by(XVerificationRequest.created_at.desc())
        )
    ).scalars().all()
    by_user: dict = {}
    for req in history:
        by_user.setdefault(req.user_id, []).append(req)
    return {
        str(req.id): [
            _request_summary(other)
            for other in by_user.get(req.user_id, [])
            if other.id != req.id
        ]
        for req, _ in rows
    }


async def _handle_clashes(db, rows) -> dict:
    """request id -> the OTHER user already holding the claimed handle.

    `approve_x_verification` refuses with a 409 in exactly this case. Before
    this, the only way to discover it was to fire the irreversible action and
    read the toast — so surface it in the list and let the UI disable Approve
    up front.
    """
    wanted = {
        (req.claimed_x_username or "").lower()
        for req, _ in rows
        if req.claimed_x_username
    }
    if not wanted:
        return {}
    holders = (
        await db.execute(
            select(User).where(func.lower(User.x_username).in_(wanted))
        )
    ).scalars().all()
    by_handle = {(u.x_username or "").lower(): u for u in holders}
    out = {}
    for req, _ in rows:
        holder = by_handle.get((req.claimed_x_username or "").lower())
        if holder is not None and holder.id != req.user_id:
            out[str(req.id)] = {
                "user_id": str(holder.id),
                "telegram_username": holder.telegram_username or "",
                "x_username": holder.x_username or "",
                "is_banned": holder.is_banned,
            }
    return out


async def list_requests(
    db,
    *,
    q: str = "",
    status: str = "PENDING",
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """Page the X-verification review queue.

    Returns ``{items, total, limit, offset}``. An empty `status` spans all
    three states, which is what the panel's history tabs read.
    """
    limit = max(1, min(int(limit), MAX_PAGE_SIZE))
    offset = max(0, int(offset))

    filters = []
    if status:
        if status not in REVIEW_STATUSES:
            raise BadRequest(f"unknown status {status!r}")
        filters.append(XVerificationRequest.status == status)

    q = (q or "").strip().lstrip("@")
    if q:
        needle = f"%{q.lower()}%"
        filters.append(
            or_(
                func.lower(XVerificationRequest.submitted_x_username).like(needle),
                func.lower(XVerificationRequest.claimed_x_username).like(needle),
                func.lower(User.telegram_username).like(needle),
                func.lower(User.x_username).like(needle),
            )
        )

    base = (
        select(XVerificationRequest, User)
        .join(User, User.id == XVerificationRequest.user_id)
        .where(*filters)
    )
    total = (
        await db.execute(
            select(func.count())
            .select_from(XVerificationRequest)
            .join(User, User.id == XVerificationRequest.user_id)
            .where(*filters)
        )
    ).scalar_one()

    rows = (
        await db.execute(
            base.order_by(XVerificationRequest.created_at.asc())
            .limit(limit)
            .offset(offset)
        )
    ).all()

    priors = await _prior_requests(db, rows)
    clashes = await _handle_clashes(db, rows)

    items = []
    for req, user in rows:
        items.append({
            **_request_summary(req),
            "user_id": str(req.user_id),
            **_user_facts(user),
            "prior_requests": priors.get(str(req.id), []),
            # non-null => approve_x_verification will 409
            "claimed_handle_taken_by": clashes.get(str(req.id)),
        })
    return {"items": items, "total": int(total), "limit": limit, "offset": offset}
