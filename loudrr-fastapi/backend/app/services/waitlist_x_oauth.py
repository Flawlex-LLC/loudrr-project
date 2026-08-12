"""X OAuth flow for the pre-signup waitlist form.

Parallel to services/x_verification.py — same underlying integrations/x_oauth.py
transport, but keyed on telegram_id (not user_id) because the applicant has no
User row yet, and the callback 302-redirects the browser back to the frontend
with a signed short-lived proof rather than rendering an HTML dead-end.

The proof is a signed itsdangerous token carrying {tg_id, x_username, x_user_id,
iat}. The frontend echoes it back on POST /waitlist/register/, which re-verifies
the signature server-side and cross-checks tg_id against the Telegram initData
to stop one user handing another's proof off.
"""
import logging
from datetime import timedelta

from fastapi.responses import RedirectResponse
from sqlalchemy import delete

from app.core.config import settings
from app.core.crypto import sign_x_proof, verify_x_proof
from app.core.errors import BadRequest, ServiceUnavailable
from app.core.time_utils import utcnow
from app.integrations import x_oauth
from app.models.waitlist_oauth_state import WaitlistOAuthState

logger = logging.getLogger(__name__)


def _waitlist_redirect_uri() -> str:
    """Callback URL the waitlist flow sends to X (must match on token exchange).

    Raises ServiceUnavailable when x_oauth_waitlist_callback_url is unset.
    We DELIBERATELY do NOT fall back to the legacy x_oauth_callback_url:
    that endpoint dispatches to the post-approval handler which reads from
    x_oauth_states (not waitlist_oauth_states), so a partial misconfig would
    silently route every waitlist authorization to the wrong handler and die
    with a generic error page. Fail loudly at start-time instead.
    """
    if not settings.x_oauth_waitlist_callback_url:
        raise ServiceUnavailable(
            "X OAuth waitlist callback URL not configured "
            "(set X_OAUTH_WAITLIST_CALLBACK_URL)"
        )
    return settings.x_oauth_waitlist_callback_url


def _frontend_origin() -> str:
    """Frontend base URL the callback 302s back to.

    Derived from miniapp_url (strip trailing `/app` if present) so ops don't
    have to maintain a second setting. Falls back to site_url, then to
    localhost:3000 for a bare dev environment.
    """
    base = (settings.miniapp_url or settings.site_url or "").rstrip("/")
    if base.endswith("/app"):
        base = base[:-4]
    return base or "http://localhost:3000"


async def start_waitlist_oauth(db, *, telegram_id: int) -> str:
    """Create a WaitlistOAuthState row + return the X authorize URL."""
    if not x_oauth.is_configured():
        raise ServiceUnavailable("X OAuth not configured")
    # Raises ServiceUnavailable if the waitlist callback URL is unset — see
    # _waitlist_redirect_uri docstring for why we don't fall back.
    redirect_uri = _waitlist_redirect_uri()

    state = x_oauth.new_state()
    verifier, challenge = x_oauth.make_pkce()
    url = x_oauth.build_authorize_url(
        state, challenge, redirect_uri=redirect_uri,
    )
    db.add(
        WaitlistOAuthState(
            state=state,
            telegram_id=telegram_id,
            code_verifier=verifier,
            expires_at=utcnow()
            + timedelta(seconds=x_oauth.STATE_TTL_SECONDS),
        )
    )
    await db.commit()
    return url


async def _consume_state(db, state: str) -> WaitlistOAuthState | None:
    """Atomically look up + delete the state row (one-time use).

    Uses ``DELETE ... RETURNING`` so that two concurrent callbacks with the
    same state can't both observe the row: exactly one caller sees the
    RETURNING row, the other gets None and returns error='expired'. Belt +
    braces on top of X's own single-use code enforcement.

    Returns None when the state is missing OR expired.
    """
    result = await db.execute(
        delete(WaitlistOAuthState)
        .where(WaitlistOAuthState.state == state)
        .returning(
            WaitlistOAuthState.state,
            WaitlistOAuthState.telegram_id,
            WaitlistOAuthState.code_verifier,
            WaitlistOAuthState.expires_at,
        )
    )
    row = result.first()
    await db.commit()
    if row is None:
        return None
    if row.expires_at < utcnow():
        return None
    return WaitlistOAuthState(
        state=row.state,
        telegram_id=row.telegram_id,
        code_verifier=row.code_verifier,
        expires_at=row.expires_at,
    )


def redirect_to_frontend(*, proof: str | None = None, error: str | None = None) -> RedirectResponse:
    frontend = _frontend_origin()
    if proof:
        return RedirectResponse(
            f"{frontend}/waitlist/oauth-return?proof={proof}", status_code=302
        )
    return RedirectResponse(
        f"{frontend}/waitlist/oauth-return?error={error or 'unknown'}",
        status_code=302,
    )


async def handle_waitlist_callback(
    db,
    *,
    code: str | None,
    state: str | None,
    error: str | None,
) -> RedirectResponse:
    """Consume state, exchange code, mint signed proof, 302 back to frontend."""
    if error:
        return redirect_to_frontend(error="denied")
    if not code or not state:
        return redirect_to_frontend(error="invalid")

    row = await _consume_state(db, state)
    if row is None:
        return redirect_to_frontend(error="expired")

    redirect_uri = _waitlist_redirect_uri()
    token = await x_oauth.exchange_code_for_token(
        code, row.code_verifier, redirect_uri=redirect_uri,
    )
    if not token:
        return redirect_to_frontend(error="token")

    me = await x_oauth.fetch_me(token)
    if not me or not me.get("username") or not me.get("id"):
        return redirect_to_frontend(error="profile")

    proof = sign_x_proof({
        "tg_id": row.telegram_id,
        "x_username": me["username"],
        "x_user_id": str(me["id"]),
        "iat": int(utcnow().timestamp()),
    })
    logger.info(
        "[WAITLIST-OAUTH] proof issued for tg_id=%s x=@%s",
        row.telegram_id, me["username"],
    )
    return redirect_to_frontend(proof=proof)


async def purge_expired_states(db) -> int:
    """Housekeeping — drop stale rows. Not wired to a cron yet."""
    result = await db.execute(
        delete(WaitlistOAuthState).where(
            WaitlistOAuthState.expires_at < utcnow()
        )
    )
    await db.commit()
    return result.rowcount or 0


def verify_and_extract(
    proof: str, *, telegram_id: int
) -> tuple[str, str]:
    """Verify a signed proof, cross-check its tg_id, and return (username, x_user_id).

    Raises BadRequest on any failure. Kept here (not in services/waitlist.py)
    so the "what a proof means" logic lives next to the code that mints it.
    """
    payload = verify_x_proof(proof)
    if not payload:
        raise BadRequest("Invalid or expired X OAuth proof")
    if payload.get("tg_id") != telegram_id:
        raise BadRequest("OAuth handle bound to a different Telegram user")
    username = payload.get("x_username")
    x_user_id = payload.get("x_user_id")
    if not username or not x_user_id:
        raise BadRequest("Malformed X OAuth proof")
    return str(username), str(x_user_id)
