import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time_utils import utcnow
from app.db.base import Base


class XOAuthConfirmation(Base):
    """An X login waiting for the browser that performed it to confirm.

    The authorize link from /x-oauth/start/ can be forwarded, and whoever
    authorizes on X is bound to whoever started the flow. So the callback no
    longer changes the user: it records what X proved here and shows a page
    naming both accounts. Only an explicit confirm applies it. One row per
    attempt; the confirm token is stored only as its sha256.
    """

    __tablename__ = "x_oauth_confirmations"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # what X's /users/me returned — the proven identity
    x_username: Mapped[str] = mapped_column(String(50))
    x_user_id: Mapped[str] = mapped_column(String(50))
    # who started the flow, as shown on the confirmation page
    telegram_label: Mapped[str] = mapped_column(String(120), default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(default=utcnow, server_default=text("now()"))
    expires_at: Mapped[datetime] = mapped_column()
