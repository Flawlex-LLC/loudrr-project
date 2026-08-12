from datetime import datetime

from sqlalchemy import BigInteger, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time_utils import utcnow
from app.db.base import Base


class WaitlistOAuthProof(Base):
    """Server-side handoff of a minted waitlist OAuth proof.

    Telegram's WebView cannot see sessionStorage written by the external
    system browser that completes the OAuth chain (openLink() opens the
    SYSTEM browser). So the callback ALSO stores the signed proof here,
    keyed by telegram_id, and the mini-app polls GET /waitlist/x-oauth/proof/
    to consume it. One row per applicant (PK on telegram_id — the callback
    upserts), 10-minute TTL enforced on read + purge.
    """

    __tablename__ = "waitlist_oauth_proofs"

    # autoincrement=False: the Telegram id is always supplied by the caller —
    # without it SQLAlchemy turns a lone integer PK into BIGSERIAL
    telegram_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False
    )
    proof: Mapped[str] = mapped_column(String(2048), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        default=utcnow, nullable=False, index=True
    )
