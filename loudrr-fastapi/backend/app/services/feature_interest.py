"""Feature-interest registration (Ch17) — endpoint 15."""
import re

from app.core.db_helpers import exists
from app.core.errors import BadRequest
from app.models.feature_interest import FeatureInterest
from app.repositories.feature_interest import FeatureInterestRepository

_FEATURE_RE = re.compile(r"^[a-zA-Z0-9_-]{1,50}$")


def _validate_feature(feature: str) -> str:
    feature = (feature or "").strip()
    if not feature:
        raise BadRequest("Feature is required")
    if not _FEATURE_RE.match(feature):
        raise BadRequest("Invalid feature name")
    return feature


async def register_interest(db, *, user, feature: str, interests) -> dict:
    feature = _validate_feature(feature)
    if not isinstance(interests, list):
        interests = []
    interests = [str(i)[:100] for i in interests[:10]]  # cap 10 × 100 chars

    repo = FeatureInterestRepository(db)
    row = await repo.get(user_id=user.id, feature=feature)
    if row is None:
        await repo.create(user_id=user.id, feature=feature, interests=interests)
    else:
        row.interests = interests  # update_or_create semantics
    await db.commit()
    return {"success": True}


async def check_interest(db, *, user, feature: str) -> dict:
    feature = _validate_feature(feature)
    registered = await exists(
        db, FeatureInterest,
        (FeatureInterest.user_id == user.id) & (FeatureInterest.feature == feature),
    )
    return {"registered": registered}
