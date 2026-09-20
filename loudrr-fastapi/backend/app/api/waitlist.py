from fastapi import APIRouter, Depends, Request, Response

from app.core.deps import get_telegram_identity
from app.core.errors import BadRequest, NotFound
from app.core.limiter import limiter, telegram_user_key
from app.db.session import get_session
from app.schemas.waitlist import (
    OAuthConfirmDecisionRequest,
    OAuthConfirmLookupRequest, OAuthConfirmDecisionResponse,
    OAuthConfirmInfoResponse, PublicCardResponse, WaitlistRegisterRequest,
    WaitlistRegisterResponse,
)
from app.services import kill_switches
from app.services import waitlist as svc
from app.services import waitlist_x_oauth as oauth_svc

# prefix="/waitlist" prepends every path → /waitlist/register/, /status/
router = APIRouter(prefix="/waitlist", tags=["waitlist"])

# The sign-up funnel is rate-limited per TELEGRAM USER (telegram_user_key),
# never per IP: carrier NAT / VPN / venue Wi-Fi put many real applicants
# behind one address, and a per-IP cap would lock them out on launch day.


# response_model= validates AND documents the output shape
@router.post("/register/", response_model=WaitlistRegisterResponse)
@limiter.limit("5/hour", key_func=telegram_user_key)
async def register(
    request: Request,                                # slowapi needs the request
    payload: WaitlistRegisterRequest,                # validated JSON body
    tg_user: dict = Depends(get_telegram_identity),  # verified caller
    db=Depends(get_session),                         # a DB session
):
    # admin kill switch — close sign-ups without a deploy. Checked here (not
    # in the service) so the OAuth steps above it stay reachable for anyone
    # mid-flow, and so services/waitlist.py stays untouched.
    await kill_switches.require_enabled(
        db, kill_switches.WAITLIST_REGISTRATION_ENABLED,
        "Waitlist sign-ups are closed right now. Check back soon.",
    )
    result = await svc.register_entry(db, tg_user=tg_user, payload=payload)
    return WaitlistRegisterResponse(
        status="registered" if result.was_new else "already_registered",
        message=(
            "Successfully registered for waitlist" if result.was_new
            else "You're already on the waitlist"
        ),
        x_username=result.entry.x_username,
        referral_code=result.entry.referral_code,
    )


@router.post("/x-oauth/start/")
@limiter.limit("10/hour", key_func=telegram_user_key)
async def start_x_oauth(
    request: Request,
    tg_user: dict = Depends(get_telegram_identity),
    db=Depends(get_session),
):
    """Kick off X OAuth for a waitlist applicant. Returns the authorize URL
    the frontend opens in an external browser via Telegram.WebApp.openLink().

    The applicant is authenticated by Telegram initData but does NOT need a
    User row (they're pre-signup). We persist a WaitlistOAuthState row keyed
    on the random `state` value; the callback consumes it, exchanges the code,
    and stores a signed proof for this applicant's poll to pick up.
    """
    telegram_id = tg_user.get("id")
    if not telegram_id:
        raise BadRequest("Missing Telegram ID")
    url = await oauth_svc.start_waitlist_oauth(
        db, telegram_id=telegram_id,
        # the confirmation page names this account to whoever authorizes on X
        telegram_label=oauth_svc.describe_telegram_user(tg_user),
    )
    return {"authorize_url": url}


@router.get("/x-oauth/proof/")
# the mini-app polls every ~2.5s (24/min) while "Waiting for X…"
@limiter.limit("40/minute", key_func=telegram_user_key)
async def poll_x_oauth_proof(
    request: Request,
    tg_user: dict = Depends(get_telegram_identity),
    db=Depends(get_session),
):
    """Poll for the outcome of this applicant's X OAuth attempt.

    Telegram's openLink() completes the OAuth chain in the SYSTEM browser, so
    the mini-app WebView can never see storage written there. The callback
    stores the outcome keyed by telegram_id; this returns
    ``{proof, x_username, expires_in, error, awaiting_confirmation}``. The
    first four are null while X is still open, and stay null (with
    awaiting_confirmation true) until the browser that authorized confirms
    the link. The proof stays readable until the applicant registers or it
    expires.
    """
    telegram_id = tg_user.get("id")
    if not telegram_id:
        raise BadRequest("Missing Telegram ID")
    return await oauth_svc.read_proof(db, telegram_id=telegram_id)


# ---- browser confirmation of an X OAuth link ----
# PUBLIC: these run in the system browser that just authorized on X, which has
# no Telegram session, so the unguessable one-time token is the only credential.
# It travels in the BODY, never the path or query: the request path is bound
# into every log line (core/request_context.py), shipped to Sentry, and kept in
# the browser's history — and this token is what releases the proof.
# Both are POST for that reason; the info call is still read-only.
# Rate-limited per IP (no Telegram identity exists here), but generously: the
# token is 256 bits, so brute force isn't the threat, while carrier NAT puts
# many real applicants behind one address on launch day.
@router.post("/x-oauth/confirm/info/", response_model=OAuthConfirmInfoResponse)
@limiter.limit("300/minute")
async def x_oauth_confirmation_info(
    request: Request,          # slowapi needs the IP
    response: Response,
    body: OAuthConfirmLookupRequest,
    db=Depends(get_session),
):
    """What the confirmation page shows: the X handle that was authorized and
    the Telegram account it would be linked to. 404 for any token that isn't
    pending (unknown, used, cancelled or expired, all with one message)."""
    response.headers["Cache-Control"] = "no-store"
    return await oauth_svc.confirmation_info(db, token=body.token)


@router.post("/x-oauth/confirm/", response_model=OAuthConfirmDecisionResponse)
@limiter.limit("120/minute")
async def x_oauth_confirmation_decide(
    request: Request,          # slowapi needs the IP
    response: Response,
    body: OAuthConfirmDecisionRequest,
    db=Depends(get_session),
):
    """The X account owner's answer. ``confirm`` releases the proof to the
    applicant's poll; ``cancel`` discards it and the poll reports "cancelled".
    Single use: the second submit of either decision is a 404."""
    response.headers["Cache-Control"] = "no-store"
    return await oauth_svc.decide_confirmation(db, token=body.token, decision=body.decision)


@router.get("/status/")
async def waitlist_status(
    tg_user: dict = Depends(get_telegram_identity),
    db=Depends(get_session),
):
    result = await svc.get_status(db, telegram_id=tg_user["id"])
    if result.status == "approved":
        return {"status": "approved"}
    if result.status in ("waitlisted", "rejected"):
        # services/waitlist.py always populates result.entry on these branches;
        # the assert lets mypy narrow Optional[WaitlistEntry] -> WaitlistEntry
        assert result.entry is not None
        body = {
            "status": result.status,
            "x_username": result.entry.x_username,
            "submitted_at": result.entry.created_at.isoformat(),
            "referral_code": result.entry.referral_code,
        }
        if result.status == "rejected":
            # the same reason the rejection Telegram message carries
            body["reason"] = result.entry.rejection_reason or ""
        return body
    return {"status": "not_registered"}


@router.get("/card/{username}/", response_model=PublicCardResponse)
# public + unauthenticated (link-preview crawlers hit the share page)
@limiter.limit("120/minute")
async def public_card(request: Request, username: str, db=Depends(get_session)):
    """Card data for the PUBLIC share page /waitlist/<username>.

    Only handles that are actually on the waitlist (or approved) resolve —
    anything else is a 404, so the page can't claim "@anyone joined". Returns
    only what the card itself shows publicly: the STORED score (never calls
    the score provider), tier, top smart-follower handles and total, plus the
    applicant's referral code for the share page's join link.
    """
    card = await svc.public_card(db, username=username)
    if card is None:
        raise NotFound("Not on the waitlist")
    return card
