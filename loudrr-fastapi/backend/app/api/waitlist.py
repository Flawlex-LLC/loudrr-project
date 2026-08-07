from fastapi import APIRouter, Depends, Request

from app.core.deps import get_telegram_identity
from app.core.limiter import limiter
from app.db.session import get_session
from app.schemas.waitlist import (
    WaitlistRegisterRequest, WaitlistRegisterResponse,
)
from app.services import waitlist as svc

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
