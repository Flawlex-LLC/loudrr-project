import asyncio

from fastapi import APIRouter, Depends, Request

from app.core.deps import get_current_user, get_telegram_identity
from app.core.limiter import limiter
from app.db.session import get_session
from app.integrations.loudrr_analytics import get_score_client
from app.models.user import User
from app.repositories.user import UserRepository
from app.repositories.waitlist import WaitlistRepository
from app.schemas.user import (
    LinkXRequest,
    LinkXResponse,
    UserInfoResponse,
    UserStatsResponse,
    WaitlistEnrichmentResponse,
)
from app.services import tier as tier_svc
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
    """Best-effort enrichment for the miniapp waitlist-pending card.

    Primary use case is the waitlist-pending screen, so the caller may have
    a WaitlistEntry only (no User row yet) — we use get_telegram_identity
    (not get_current_user) and resolve x_username from either table.

    Never 500s: on any failure (no handle, analytics down, network error,
    malformed payload) returns the empty shape.
    """
    empty = WaitlistEnrichmentResponse(
        x_username=None, score=None, tier=None, followers=[], followers_count=0
    )

    tg_id = tg_user.get("id")
    if not tg_id:
        return empty

    # Approved user -> User row; still-waitlisted -> WaitlistEntry.
    x_uname: str | None = None
    user = await UserRepository(db).get(telegram_id=tg_id)
    if user is not None:
        x_uname = user.x_username
    else:
        entry = await WaitlistRepository(db).get(telegram_id=tg_id)
        if entry is not None:
            x_uname = entry.x_username

    x_uname = (x_uname or "").strip().lstrip("@")
    if not x_uname:
        return empty

    try:
        client = get_score_client()
        profile, top = await asyncio.gather(
            client.get_user_data(x_uname),
            client.get_top_followers(x_uname, k=10),
        )
    except Exception:
        return empty

    score: float | None = None
    if profile and profile.get("score") is not None:
        try:
            score = float(profile["score"])
        except (TypeError, ValueError):
            score = None

    followers: list[str] = []
    for u in (top or []):
        name = (u.get("username") or "").strip().lstrip("@")
        if name and name not in followers:
            followers.append(name)
        if len(followers) >= 10:
            break

    return WaitlistEnrichmentResponse(
        x_username=x_uname,
        score=score,
        tier=tier_svc.tier_for(score) if score is not None else None,
        followers=followers,
        followers_count=len(followers),
    )
