from app.models.waitlist_entry import WaitlistEntry
from app.repositories.base import BaseRepository

class WaitlistRepository(BaseRepository[WaitlistEntry]):
    model = WaitlistEntry