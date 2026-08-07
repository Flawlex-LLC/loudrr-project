from fastapi import APIRouter, Depends

from app.core.deps import get_current_user
from app.db.session import get_session
from app.models.user import User
from app.schemas.session import SessionPostRequest
from app.services import sessions as svc

router = APIRouter(tags=["session"])


@router.post("/session/start/")
async def session_start(
    user: User = Depends(get_current_user),
    db=Depends(get_session),
):
    return await svc.start_session(db, user=user)


@router.post("/session/click/")
async def session_click(
    payload: SessionPostRequest,
    user: User = Depends(get_current_user),
    db=Depends(get_session),
):
    return await svc.record_click(db, user=user, post_id=payload.post_id)


@router.post("/session/verify-return/")
async def session_verify_return(
    payload: SessionPostRequest,
    user: User = Depends(get_current_user),
    db=Depends(get_session),
):
    return await svc.verify_return(db, user=user, post_id=payload.post_id)
