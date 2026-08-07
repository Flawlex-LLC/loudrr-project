from app.models.x_verification_request import XVerificationRequest
from app.repositories.base import BaseRepository


class XVerificationRequestRepository(BaseRepository[XVerificationRequest]):
    model = XVerificationRequest
