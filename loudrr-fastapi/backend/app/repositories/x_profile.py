from app.models.x_profile import XProfile
from app.repositories.base import BaseRepository


class XProfileRepository(BaseRepository[XProfile]):
    model = XProfile
