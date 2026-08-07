import uuid
from pydantic import BaseModel


class SessionPostRequest(BaseModel):
    """Body for /session/click/ and /session/verify-return/."""
    post_id: uuid.UUID
