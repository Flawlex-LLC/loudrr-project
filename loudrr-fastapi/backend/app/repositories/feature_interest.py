from app.models.feature_interest import FeatureInterest
from app.repositories.base import BaseRepository


class FeatureInterestRepository(BaseRepository[FeatureInterest]):
    model = FeatureInterest
