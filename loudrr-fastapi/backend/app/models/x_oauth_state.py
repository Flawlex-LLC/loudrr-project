import uuid
from datetime import datetime
from sqlalchemy import String, ForeignKey, text
from sqlalchemy.orm import Mapped, mapped_column
from app.core.time_utils import utcnow
from app.db.base import Base


class XOAuthState(Base):
    """Short-lived PKCE state for the X OAuth flow (Ch11).

    `/x-oauth/start/` writes one row (the random `state` → the user and the
    PKCE `code_verifier`); the public callback reads it back by `state`,
    deletes it, and exchanges the code. A DB row (rather than an in-process
    cache) means the flow survives a restart and works across instances —
    `start` and the callback need not hit the same worker.
    """

    __tablename__ = "x_oauth_states"

    # the random opaque `state` value sent to X and echoed back
    state: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    code_verifier: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        default=utcnow, server_default=text("now()")
    )
    # rows past this are stale and rejected (10-minute TTL set on creation)
    expires_at: Mapped[datetime] = mapped_column()
