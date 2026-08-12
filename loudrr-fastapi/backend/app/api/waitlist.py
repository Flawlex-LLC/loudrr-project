from fastapi import APIRouter, Depends, Request

from app.core.deps import get_telegram_identity
from app.core.errors import BadRequest
from app.core.limiter import limiter
from app.db.session import get_session
from app.schemas.waitlist import (
    WaitlistRegisterRequest, WaitlistRegisterResponse,
)
from app.services import waitlist as svc
from app.services import waitlist_x_oauth as oauth_svc

# prefix="/waitlist" prepends every path → /waitlist/register/, /status/
router = APIRouter(prefix="/waitlist", tags=["waitlist"])


# response_model= validates AND documents the output shape
@router.post("/register/", response_model=WaitlistRegisterResponse)
# slowapi: max 5 calls/hour per IP, else an automatic 429
@limiter.limit("5/hour")
async def register(
    request: Request,                                # slowapi needs the IP
    payload: WaitlistRegisterRequest,                # validated JSON body
    tg_user: dict = Depends(get_telegram_identity),  # verified caller
    db=Depends(get_session),                         # a DB session
):
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
@limiter.limit("10/hour")
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
    and mints a signed proof the frontend echoes to /waitlist/register/.
    """
    telegram_id = tg_user.get("id")
    if not telegram_id:
        raise BadRequest("Missing Telegram ID")
    url = await oauth_svc.start_waitlist_oauth(db, telegram_id=telegram_id)
    return {"authorize_url": url}


@router.get("/x-oauth/proof/")
@limiter.limit("30/minute")
async def poll_x_oauth_proof(
    request: Request,
    tg_user: dict = Depends(get_telegram_identity),
    db=Depends(get_session),
):
    """One-time poll for a server-side stored OAuth proof.

    Telegram's openLink() completes the OAuth chain in the SYSTEM browser, so
    the mini-app WebView can never see the sessionStorage written there. The
    callback also upserts the minted proof keyed by telegram_id; the mini-app
    polls this endpoint and receives the proof exactly once (atomic
    DELETE ... RETURNING), or null when there's nothing (yet) to consume.
    """
    telegram_id = tg_user.get("id")
    if not telegram_id:
        raise BadRequest("Missing Telegram ID")
    proof = await oauth_svc.consume_proof(db, telegram_id=telegram_id)
    return {"proof": proof}


@router.get("/status/")
async def waitlist_status(
    tg_user: dict = Depends(get_telegram_identity),
    db=Depends(get_session),
):
    result = await svc.get_status(db, telegram_id=tg_user["id"])
    if result.status == "approved":
        return {"status": "approved"}
    if result.status == "waitlisted":
        # services/waitlist.py:151 always populates result.entry on this branch;
        # the assert lets mypy narrow Optional[WaitlistEntry] -> WaitlistEntry
        assert result.entry is not None
        # .isoformat() makes the datetime JSON-friendly
        return {
            "status": "waitlisted",
            "x_username": result.entry.x_username,
            "submitted_at": result.entry.created_at.isoformat(),
            "referral_code": result.entry.referral_code,
        }
    return {"status": "not_registered"}
