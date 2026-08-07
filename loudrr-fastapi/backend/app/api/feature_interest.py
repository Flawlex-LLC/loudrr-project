from fastapi import APIRouter, Depends, Query

from app.core.deps import get_current_user
from app.db.session import get_session
from app.models.user import User
from app.schemas._feature import FeatureInterestRequest
from app.services import feature_interest as svc

router = APIRouter(tags=["feature-interest"])


@router.post("/feature-interest/")
async def register_feature_interest(
    payload: FeatureInterestRequest,
    user: User = Depends(get_current_user),
    db=Depends(get_session),
):
    return await svc.register_interest(
        db, user=user, feature=payload.feature, interests=payload.interests
    )


@router.get("/feature-interest/")
async def check_feature_interest(
    feature: str = Query(...),
    user: User = Depends(get_current_user),
    db=Depends(get_session),
):
    return await svc.check_interest(db, user=user, feature=feature)
