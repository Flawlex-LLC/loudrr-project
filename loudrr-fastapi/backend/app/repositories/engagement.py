from app.models.engagement import Engagement
from app.repositories.base import BaseRepository


class EngagementRepository(BaseRepository[Engagement]):
    model = Engagement
