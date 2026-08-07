from fastapi import APIRouter, Depends, Request

from app.core.deps import get_current_user
from app.core.limiter import limiter
from app.db.session import get_session
from app.models.user import User
from app.schemas.user import (
    LinkXRequest,
    LinkXResponse,
    UserInfoResponse,
    UserStatsResponse,
)
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
