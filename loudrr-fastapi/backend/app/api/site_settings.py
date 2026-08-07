from fastapi import APIRouter, Depends
from app.services.site_settings import get_setting
from app.db.session import get_session

router = APIRouter(prefix="/site_settings", tags=["site_settings"])


@router.get("")
async def read_settings(db=Depends(get_session)):
    return {
        "post_cost_min": await get_setting(db, "POST_COST_MIN"),
        "post_cost_max": await get_setting(db, "POST_COST_MAX"),
        "post_cost": await get_setting(db, "POST_COST"),
        "credit_per_engagement": await get_setting(db, "CREDIT_PER_ENGAGEMENT"),
        # more can be added
    }
