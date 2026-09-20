"""X OAuth flow for the pre-signup waitlist form.

Parallel to services/x_verification.py — same underlying integrations/x_oauth.py
transport, but keyed on telegram_id (not user_id) because the applicant has no
User row yet, and the callback 302-redirects the browser back to the frontend
rather than rendering an HTML dead-end.

The proof is a signed itsdangerous token carrying {tg_id, x_username, x_user_id,
iat}. The frontend echoes it back on POST /waitlist/register/, which re-verifies
the signature server-side and cross-checks tg_id against the Telegram initData
to stop one user handing another's proof off.

Browser confirmation: the X authorize URL can be forwarded. Someone could start
Connect X in their own mini-app and send the link to a victim, and when the
victim authorized, the victim's X account would be bound to their Telegram. So
the callback stores the proof UNCONFIRMED and sends the browser that authorized
on X to a page that names the Telegram account that started the flow. Nothing
is released until that browser confirms: the poll reports only
``awaiting_confirmation`` and register refuses the proof.
"""
import hashlib
import hmac
import logging
import re
import secrets
import unicodedata
from datetime import timedelta

from fastapi.responses import RedirectResponse
from sqlalchemy import delete, select, update

from app.core.config import settings
from app.core.crypto import sign_x_proof, verify_x_proof
from app.core.errors import BadRequest, NotFound, ServiceUnavailable
from app.core.time_utils import utcnow
from app.integrations import x_oauth
from app.models.waitlist_oauth_proof import WaitlistOAuthProof
from app.models.waitlist_oauth_state import WaitlistOAuthState

# how long a stored proof row stays consumable — matches the proof's own
# itsdangerous max_age (crypto.verify_x_proof default 600s), so the poll
# endpoint never hands out a token the register endpoint would reject.
# The confirmation window is the same 10 minutes, counted from the callback.
PROOF_TTL_SECONDS = 600

# Unknown, used, expired: one answer for all, so the endpoint doesn't reveal
# which tokens ever existed.
CONFIRM_LINK_GONE = "This confirmation link is invalid, already used or expired"

# secrets.token_urlsafe(32) is 43 characters; anything far longer is junk
# and isn't worth hashing
_CONFIRM_TOKEN_MAX_LEN = 128

logger = logging.getLogger(__name__)


# ---- who started the flow (shown on the browser confirmation page) ----
_LABEL_MAX_LEN = 120   # waitlist_oauth_states/proofs.telegram_label
_NAME_MAX_LEN = 64


def fallback_telegram_label(telegram_id: int | None) -> str:
    return f"Telegram user {telegram_id}"


def _display_name(tg_user: dict) -> str:
    """First + last name, safe to show to a stranger.

    A display name is free text chosen by whoever started the flow, and the
    person reading it may be their target. So:
    * NFKC first, which folds lookalikes such as fullwidth "＠" and "（）"
      into their ASCII forms before the filters below;
    * control and format characters become spaces, which kills bidi
      overrides and zero-width tricks;
    * "@" is dropped, so an "@handle" in the label can only be the account's
      real, verified username;
    * parentheses are dropped, so the name can't fake the "(…)" around itself.
    """
    raw = unicodedata.normalize(
        "NFKC", f"{tg_user.get('first_name') or ''} {tg_user.get('last_name') or ''}"
    )
    cleaned = "".join(
        " " if unicodedata.category(ch)[0] in ("C", "Z") else ch
        for ch in raw
        if ch not in "@()"
    )
    name = " ".join(cleaned.split())
    if len(name) > _NAME_MAX_LEN:
        name = name[: _NAME_MAX_LEN - 1].rstrip() + "…"
    return name


def describe_telegram_user(tg_user: dict) -> str:
    """Label for the verified initData user who pressed Connect X.

    Gives "@alice (Alice B)", "@alice", "Alice B", or "Telegram user 42" when
    there's nothing else.
    """
    # Telegram usernames are [A-Za-z0-9_]{5,32}. Keeping only those characters
    # means the username part can't carry spaces or brackets.
    username = re.sub(r"[^A-Za-z0-9_]", "", str(tg_user.get("username") or ""))[:32]
    name = _display_name(tg_user)
    if username and name:
        label = f"@{username} ({name})"
    elif username:
        label = f"@{username}"
    else:
        label = name or fallback_telegram_label(tg_user.get("id"))
    return label[:_LABEL_MAX_LEN]


def _hash_confirm_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


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


def _frontend_origin() -> str:  # noqa: D401
    """Frontend base URL the callback 302s back to.

    Derived from miniapp_url (strip trailing `/app` if present) so ops don't
    have to maintain a second setting. Falls back to site_url, then to
    localhost:3000 for a bare dev environment.
    """
    base = (settings.miniapp_url or settings.site_url or "").rstrip("/")
    if base.endswith("/app"):
        base = base[:-4]
    if base:
        return base
    # the redirect is now the ONLY way the confirmation token reaches a human:
    # a wrong origin stalls every sign-up with nothing in the logs
    if not settings.debug:
        raise ServiceUnavailable(
            "Frontend URL not configured (set MINIAPP_URL or SITE_URL)"
        )
    return "http://localhost:3000"


async def start_waitlist_oauth(db, *, telegram_id: int, telegram_label: str = "") -> str:
    """Create a WaitlistOAuthState row + return the X authorize URL.

    ``telegram_label`` names the caller (``describe_telegram_user``). The
    confirmation page shows it to whoever ends up authorizing on X.
    """
    # opportunistic housekeeping — cheap indexed DELETE of stale state/proof
    # rows, so they can't accumulate unbounded (there's no cron wired up)
    await purge_expired_states(db)
    # Deliberately NOT x_oauth.is_configured(): that also requires the LEGACY
    # x_oauth_callback_url, which the waitlist flow never uses. Only the
    # client id matters here — _waitlist_redirect_uri() below already fails
    # loudly when the waitlist callback URL is unset.
    if not settings.x_oauth_client_id:
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
            telegram_label=(
                telegram_label or fallback_telegram_label(telegram_id)
            )[:_LABEL_MAX_LEN],
            expires_at=utcnow()
            + timedelta(seconds=x_oauth.STATE_TTL_SECONDS),
        )
    )
    await db.commit()
    return url


async def _consume_state(db, state: str) -> tuple[WaitlistOAuthState | None, int | None]:
    """Atomically look up + delete the state row (one-time use).

    Uses ``DELETE ... RETURNING`` so that two concurrent callbacks with the
    same state can't both observe the row: exactly one caller sees the
    RETURNING row, the other gets None and returns error='expired'. Belt +
    braces on top of X's own single-use code enforcement.

    Returns ``(state, telegram_id)``: state is None when missing OR expired;
    telegram_id is known whenever the row existed (expired included), so the
    callback can still tell that applicant's mini-app what went wrong.
    """
    result = await db.execute(
        delete(WaitlistOAuthState)
        .where(WaitlistOAuthState.state == state)
        .returning(
            WaitlistOAuthState.state,
            WaitlistOAuthState.telegram_id,
            WaitlistOAuthState.code_verifier,
            WaitlistOAuthState.telegram_label,
            WaitlistOAuthState.expires_at,
        )
    )
    row = result.first()
    await db.commit()
    if row is None:
        return None, None
    if row.expires_at < utcnow():
        return None, row.telegram_id
    return WaitlistOAuthState(
        state=row.state,
        telegram_id=row.telegram_id,
        code_verifier=row.code_verifier,
        telegram_label=row.telegram_label,
        expires_at=row.expires_at,
    ), row.telegram_id


async def _store_outcome(
    db,
    telegram_id: int | None,
    *,
    proof: str = "",
    error: str | None = None,
    confirm_token_hash: str | None = None,
    telegram_label: str = "",
) -> None:
    """Upsert the one handoff row for this applicant (DELETE-then-INSERT — a
    retried authorize simply replaces the previous proof or error, and with
    it any confirm link issued for the previous one)."""
    if telegram_id is None:
        return
    await db.execute(
        delete(WaitlistOAuthProof).where(WaitlistOAuthProof.telegram_id == telegram_id)
    )
    db.add(WaitlistOAuthProof(
        telegram_id=telegram_id, proof=proof, error=error,
        confirm_token_hash=confirm_token_hash, telegram_label=telegram_label,
    ))
    await db.commit()


def redirect_to_frontend(*, error: str | None = None, confirm: str | None = None) -> RedirectResponse:
    """302 the SYSTEM browser to /waitlist/oauth-return.

    Success carries the one-time confirm token in the URL FRAGMENT
    (``#confirm=``), which browsers never send to a server: not to the
    frontend's access logs, not in a Referer header, not to an analytics tag.
    The page reads it and strips it from the address bar immediately. The
    token is single-use, dies with the proof, and can do nothing but confirm
    or cancel this one link; the proof itself never travels in a URL at all
    (the mini-app collects it via the server-side handoff once confirmed).
    A failure is not a secret, so it stays a normal query param.
    """
    frontend = _frontend_origin()
    if confirm:
        return RedirectResponse(
            f"{frontend}/waitlist/oauth-return#confirm={confirm}", status_code=302
        )
    return RedirectResponse(
        f"{frontend}/waitlist/oauth-return?error={error or 'internal'}", status_code=302
    )


async def handle_waitlist_callback(
    db,
    *,
    code: str | None,
    state: str | None,
    error: str | None,
) -> RedirectResponse:
    """Consume state, exchange code, mint signed proof, 302 back to frontend.

    Every outcome is also written to the server-side handoff row when we know
    whose attempt it was, so the mini-app's poll ends on a proof OR an error —
    never "Waiting for X…" forever. A minted proof is stored unconfirmed; the
    redirect takes this browser to the page that confirms or cancels it.
    """
    row, telegram_id = await _consume_state(db, state) if state else (None, None)
    if error:
        failure = "denied"      # the user said no on X
    elif not code or not state:
        failure = "invalid"
    elif row is None:
        failure = "expired"     # unknown, reused or timed-out state
    else:
        failure = None
    if failure:
        await _store_outcome(db, telegram_id, error=failure)
        return redirect_to_frontend(error=failure)
    assert row is not None and code

    redirect_uri = _waitlist_redirect_uri()
    token = await x_oauth.exchange_code_for_token(
        code, row.code_verifier, redirect_uri=redirect_uri,
    )
    if not token:
        await _store_outcome(db, row.telegram_id, error="token")
        return redirect_to_frontend(error="token")

    me = await x_oauth.fetch_me(token)
    if not me or not me.get("username") or not me.get("id"):
        await _store_outcome(db, row.telegram_id, error="profile")
        return redirect_to_frontend(error="profile")

    proof = sign_x_proof({
        "tg_id": row.telegram_id,
        "x_username": me["username"],
        "x_user_id": str(me["id"]),
        "iat": int(utcnow().timestamp()),
    })
    # Server-side handoff for the Telegram WebView: openLink() completed this
    # OAuth chain in the SYSTEM browser, whose storage the mini-app can never
    # read. Store the proof keyed by telegram_id BEFORE the redirect, so the
    # mini-app's poll of /waitlist/x-oauth/proof/ finds it. It stays withheld
    # until THIS browser, the one that just authorized on X, confirms it for
    # the Telegram account named on the page. Only the token's hash is stored.
    confirm_token = secrets.token_urlsafe(32)
    await _store_outcome(
        db, row.telegram_id, proof=proof,
        confirm_token_hash=_hash_confirm_token(confirm_token),
        telegram_label=row.telegram_label or fallback_telegram_label(row.telegram_id),
    )

    logger.info(
        "[WAITLIST-OAUTH] proof issued for tg_id=%s x=@%s, awaiting browser confirmation",
        row.telegram_id, me["username"],
    )
    return redirect_to_frontend(confirm=confirm_token)


async def read_proof(db, *, telegram_id: int) -> dict:
    """What the mini-app's poll sees:
    {"proof", "x_username", "expires_in", "error", "awaiting_confirmation"}.

    Not destructive for a proof: a dropped poll response or a WebView restart
    must not cost the applicant another trip to X. The proof is bound to this
    telegram_id by its signature and the row is deleted when they register
    (``discard_proof``). An error is reported once, then cleared, so the next
    Connect X starts clean ("cancelled" included). ``x_username`` comes from
    the verified proof — the frontend never has to decode the (compressed)
    token itself.

    An UNCONFIRMED proof is withheld entirely: the answer is the empty payload
    plus ``awaiting_confirmation``. Even the handle stays hidden, because until
    the browser confirms, the X account may belong to someone who never meant
    to link it here.
    """
    empty = {
        "proof": None, "x_username": None, "expires_in": None, "error": None,
        "awaiting_confirmation": False,
    }
    row = (
        await db.execute(
            select(WaitlistOAuthProof)
            .where(WaitlistOAuthProof.telegram_id == telegram_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if row is None:
        return empty
    age = (utcnow() - row.created_at).total_seconds()
    if row.error or age >= PROOF_TTL_SECONDS:
        return await _report_once(db, row, row.error or "expired", empty)
    if row.confirmed_at is None:
        if row.confirm_token_hash is None:
            # Stored before browser confirmation existed, so no token can ever
            # confirm it. It's dead: say so now rather than after the TTL.
            return await _report_once(db, row, "expired", empty)
        return empty | {"awaiting_confirmation": True}
    payload = verify_x_proof(row.proof)
    if not payload:
        return await _report_once(db, row, "expired", empty)
    return {
        "proof": row.proof,
        "x_username": payload.get("x_username"),
        "expires_in": max(0, int(PROOF_TTL_SECONDS - age)),
        "error": None,
        "awaiting_confirmation": False,
    }


async def _report_once(db, row: WaitlistOAuthProof, error: str, empty: dict) -> dict:
    """Clear a dead/error row and report it. Only THAT row is deleted
    (matched on created_at): a callback that replaced it in the meantime has
    stored a newer attempt, which must survive to be confirmed."""
    await db.execute(
        delete(WaitlistOAuthProof).where(
            WaitlistOAuthProof.telegram_id == row.telegram_id,
            WaitlistOAuthProof.created_at == row.created_at,
        )
    )
    await db.commit()
    return empty | {"error": error}


# ---- browser confirmation (public: the system browser has no Telegram) ----
def _pending_confirmation(token: str) -> tuple:
    """WHERE criteria for the unconfirmed, unexpired proof ``token`` confirms.

    Raises NotFound for a token that can't be one of ours, so junk never
    reaches the database.
    """
    if not token or len(token) > _CONFIRM_TOKEN_MAX_LEN:
        raise NotFound(CONFIRM_LINK_GONE)
    return (
        WaitlistOAuthProof.confirm_token_hash == _hash_confirm_token(token),
        WaitlistOAuthProof.confirmed_at.is_(None),
        WaitlistOAuthProof.error.is_(None),
        # the same 10 minutes the poll allows, counted from the callback
        WaitlistOAuthProof.created_at > utcnow() - timedelta(seconds=PROOF_TTL_SECONDS),
    )


async def confirmation_info(db, *, token: str) -> dict:
    """What the confirmation page shows: the X account that was just
    authorized, and the Telegram account it would be linked to.

    Read-only. Any token that isn't pending (unknown, used, cancelled,
    superseded by a newer attempt, expired) gets the same NotFound.
    """
    row = (
        await db.execute(
            select(
                WaitlistOAuthProof.telegram_id,
                WaitlistOAuthProof.proof,
                WaitlistOAuthProof.telegram_label,
                WaitlistOAuthProof.created_at,
            ).where(*_pending_confirmation(token))
        )
    ).first()
    payload = verify_x_proof(row.proof) if row is not None else None
    if row is None or not payload or not payload.get("x_username"):
        raise NotFound(CONFIRM_LINK_GONE)
    age = (utcnow() - row.created_at).total_seconds()
    return {
        "x_username": str(payload["x_username"]),
        "telegram_label": row.telegram_label or fallback_telegram_label(row.telegram_id),
        "expires_in": max(0, int(PROOF_TTL_SECONDS - age)),
    }


async def decide_confirmation(db, *, token: str, decision: str) -> dict:
    """Apply the answer given on the confirmation page.

    confirm: stamp ``confirmed_at`` and clear the token hash (single use), so
    the poll releases the proof and register accepts it.
    cancel: drop the proof and leave a "cancelled" error for the poll (the
    ``_store_outcome`` semantics).

    Each decision is ONE guarded statement (UPDATE / DELETE ... WHERE the token
    is still pending). A double submit, or a confirm racing a cancel, changes
    the row at most once. Postgres re-checks the WHERE after the row lock is
    released, so the loser matches nothing and gets the same NotFound as an
    unknown token.
    """
    where = _pending_confirmation(token)
    if decision == "confirm":
        now = utcnow()
        confirmed = (
            await db.execute(
                update(WaitlistOAuthProof)
                .where(*where)
                .values(confirmed_at=now, confirm_token_hash=None)
                .returning(WaitlistOAuthProof.telegram_id, WaitlistOAuthProof.proof)
            )
        ).first()
        if confirmed is None:
            await db.commit()   # matched nothing; just end the transaction
            raise NotFound(CONFIRM_LINK_GONE)
        # Re-mint from this moment: reading the page and switching back to
        # Telegram eats the same 600s the applicant still needs to fill the
        # form in, so a slow reader used to land on an already-dead proof.
        payload = verify_x_proof(confirmed.proof)
        if payload:
            await db.execute(
                update(WaitlistOAuthProof)
                .where(
                    WaitlistOAuthProof.telegram_id == confirmed.telegram_id,
                    WaitlistOAuthProof.confirmed_at == now,
                )
                .values(
                    proof=sign_x_proof({**payload, "iat": int(now.timestamp())}),
                    created_at=now,
                )
            )
        await db.commit()
        logger.info("[WAITLIST-OAUTH] proof confirmed in browser for tg_id=%s", confirmed.telegram_id)
        return {"ok": True, "status": "confirmed"}
    if decision != "cancel":
        raise BadRequest("decision must be 'confirm' or 'cancel'")
    cancelled = (
        await db.execute(
            delete(WaitlistOAuthProof)
            .where(*where)
            .returning(WaitlistOAuthProof.telegram_id)
        )
    ).first()
    if cancelled is None:
        await db.commit()   # matched nothing; just end the transaction
        raise NotFound(CONFIRM_LINK_GONE)
    # same transaction as the guarded DELETE: nothing can slip in between
    await _store_outcome(db, cancelled.telegram_id, error="cancelled")
    logger.info("[WAITLIST-OAUTH] proof cancelled in browser for tg_id=%s", cancelled.telegram_id)
    return {"ok": True, "status": "cancelled"}


async def discard_proof(db, *, telegram_id: int) -> None:
    """Drop the applicant's handoff row — part of the caller's transaction
    (register commits it together with the new entry)."""
    await db.execute(
        delete(WaitlistOAuthProof).where(WaitlistOAuthProof.telegram_id == telegram_id)
    )


async def purge_expired_states(db) -> int:
    """Housekeeping — drop stale state + proof rows.

    Called opportunistically from start_waitlist_oauth (both DELETEs hit an
    index) so neither table can accumulate unbounded.
    """
    result = await db.execute(
        delete(WaitlistOAuthState).where(
            WaitlistOAuthState.expires_at < utcnow()
        )
    )
    proof_result = await db.execute(
        delete(WaitlistOAuthProof).where(
            WaitlistOAuthProof.created_at
            < utcnow() - timedelta(seconds=PROOF_TTL_SECONDS)
        )
    )
    await db.commit()
    return (result.rowcount or 0) + (proof_result.rowcount or 0)


def _verify_and_extract(
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


async def verify_confirmed_proof(
    db, proof: str, *, telegram_id: int
) -> tuple[str, str]:
    """Register's gate: ``_verify_and_extract`` (signature, freshness, tg_id
    binding, shape), plus a check that this applicant's handoff row is
    CONFIRMED and holds exactly this proof.

    The positive check is what makes a proof that leaked before confirmation
    worthless. "Refuse if a row is pending" would not be enough. Cancelling,
    starting another Connect X (which replaces the row) or waiting for a purge
    all make the pending row disappear, and the leaked token would then pass.
    Raises BadRequest; the message mentions "OAuth proof" so the mini-app
    sends the applicant back to Connect X.
    """
    x_username, x_user_id = _verify_and_extract(proof, telegram_id=telegram_id)
    row = (
        await db.execute(
            select(
                WaitlistOAuthProof.proof,
                WaitlistOAuthProof.error,
                WaitlistOAuthProof.confirmed_at,
            ).where(WaitlistOAuthProof.telegram_id == telegram_id)
        )
    ).first()
    if (
        row is None
        or row.error is not None
        or row.confirmed_at is None
        or not hmac.compare_digest(row.proof.encode(), proof.encode())
    ):
        raise BadRequest("X OAuth proof is not confirmed — connect X again")
    return x_username, x_user_id
