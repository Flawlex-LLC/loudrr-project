from fastapi import APIRouter, Depends, Request

from app.core.deps import get_current_user, get_telegram_identity
from app.core.errors import BadRequest
from app.core.limiter import limiter, telegram_user_key
from app.db.session import get_session
from app.models.user import User
from app.repositories.user import UserRepository
from app.repositories.waitlist import WaitlistRepository
from app.schemas.user import (
    LinkXRequest,
    LinkXResponse,
    RefreshScoreResponse,
    UserInfoResponse,
    UserStatsResponse,
    WaitlistEnrichmentResponse,
)
from app.services import scores as scores_svc
from app.services import users as svc

# No prefix: these paths sit at the API root (the Next.js frontend proxies
# /api/miniapp/* here), so the contract paths are /user/, /user/stats/, etc.
router = APIRouter(tags=["user"])


@router.get("/user/", response_model=UserInfoResponse)
async def user_info(
    user: User = Depends(get_current_user),
    db=Depends(get_session),
):
    return await svc.build_user_info(db, user=user)


@router.get("/user/stats/", response_model=UserStatsResponse)
async def user_stats(
    user: User = Depends(get_current_user),
    db=Depends(get_session),
):
    return await svc.build_user_stats(db, user=user)


@router.post("/user/link-x/", response_model=LinkXResponse)
# paid TweetScout call → cap per-IP to limit quota burn / abuse
@limiter.limit("10/hour")
async def link_x(
    request: Request,
    payload: LinkXRequest,
    user: User = Depends(get_current_user),
    db=Depends(get_session),
):
    return await svc.link_x_account(db, user=user, x_username=payload.x_username)


@router.post("/onboarding/complete/")
async def onboarding_complete(
    user: User = Depends(get_current_user),
    db=Depends(get_session),
):
    # polymorphic response (already-onboarded vs fetched vs API-down) — returned
    # as a plain dict so only the keys for each case are present, matching the
    # frontend's expectations exactly
    return await svc.complete_onboarding(db, user=user)


@router.get("/user/waitlist-enrichment/", response_model=WaitlistEnrichmentResponse)
async def waitlist_enrichment(
    tg_user: dict = Depends(get_telegram_identity),
    db=Depends(get_session),
):
    """The caller's STORED score for the miniapp waitlist card.

    Primary use case is the waitlist-pending screen, so the caller may have
    a WaitlistEntry only (no User row yet) — we use get_telegram_identity
    (not get_current_user) and resolve the handle from either table.

    Never calls the score provider: scores are fetched at sign-up and by
    POST /user/refresh-score/ only. score_status "pending" means the sign-up
    fetch hasn't landed yet (the screen polls briefly). Never 500s — no
    handle / no record returns the empty shape.
    """
    empty = WaitlistEnrichmentResponse(
        x_username=None, score=None, tier=None, followers=[], followers_count=0,
    )

    tg_id = tg_user.get("id")
    if not tg_id:
        return empty

    # Approved user -> User row; still-waitlisted -> WaitlistEntry.
    user = await UserRepository(db).get(telegram_id=tg_id)
    entry = None if user is not None else await WaitlistRepository(db).get(telegram_id=tg_id)
    owner = user or entry
    x_uname = ((owner.x_username if owner else "") or "").strip().lstrip("@")
    if not x_uname:
        return empty

    card = await scores_svc.stored_card(db, user=user, entry=entry)
    return WaitlistEnrichmentResponse(x_username=x_uname, **card)


@router.post("/user/refresh-score/", response_model=RefreshScoreResponse)
# the per-account cooldown (SCORE_REFRESH_COOLDOWN_MINUTES) is the real limit;
# this cap (per Telegram user — many users share IPs) only stops hammering
@limiter.limit("30/hour", key_func=telegram_user_key)
async def refresh_score(
    request: Request,
    tg_user: dict = Depends(get_telegram_identity),
    db=Depends(get_session),
):
    """The mini-app "Refresh score" button — works for applicants and
    approved users. Returns the card fields plus what happened (``result``)."""
    tg_id = tg_user.get("id")
    if not tg_id:
        raise BadRequest("Missing Telegram ID")
    return await scores_svc.refresh_score(db, telegram_id=tg_id)
