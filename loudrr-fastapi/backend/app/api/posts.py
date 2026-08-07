from fastapi import APIRouter, Depends, Request

from app.core.deps import get_current_user
from app.core.limiter import limiter
from app.db.session import get_session
from app.models.user import User
from app.schemas.post import PostSubmitRequest, PostSubmitResponse
from app.services import posts as svc

router = APIRouter(tags=["posts"])


@router.post("/post/submit/", response_model=PostSubmitResponse)
# paid Twitter content fetch + spends karma → throttle per IP
@limiter.limit("20/hour")
async def submit_post(
    request: Request,
    payload: PostSubmitRequest,
    user: User = Depends(get_current_user),
    db=Depends(get_session),
):
    return await svc.submit_post(
        db, user=user, x_link=payload.x_link, karma_amount=payload.karma_amount
    )
