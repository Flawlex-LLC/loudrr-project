from datetime import datetime

from sqlalchemy import BigInteger, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time_utils import utcnow
from app.db.base import Base


class WaitlistOAuthState(Base):
    """Short-lived PKCE state for the pre-signup waitlist X OAuth flow.

    Mirrors x_oauth_states but keyed on telegram_id (BigInteger) rather than
    user_id, because waitlist applicants don't have a User row yet. The state
    row is created by POST /waitlist/x-oauth/start/ and consumed (deleted) by
    GET /api/auth/x/callback/waitlist/. 10-minute TTL, one-time use.
    """

    __tablename__ = "waitlist_oauth_states"

    state: Mapped[str] = mapped_column(String(64), primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    code_verifier: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        default=utcnow, server_default=text("now()"), nullable=False
    )
    # rows past this are stale and rejected (10-minute TTL set on creation)
    expires_at: Mapped[datetime] = mapped_column(index=True, nullable=False)
