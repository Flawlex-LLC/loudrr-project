from app.models.verification_batch import VerificationBatch
from app.repositories.base import BaseRepository


class VerificationBatchRepository(BaseRepository[VerificationBatch]):
    model = VerificationBatch
