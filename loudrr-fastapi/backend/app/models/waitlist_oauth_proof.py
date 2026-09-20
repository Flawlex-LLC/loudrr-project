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
    to read it. One row per applicant (PK on telegram_id — the callback
    upserts), 10-minute TTL enforced on read + purge. The row is deleted when
    the applicant registers, not when polled (a dropped poll response must
    not cost them another trip to X).

    When the OAuth attempt FAILS (denied on X, expired, token/profile error)
    the row carries ``error`` instead (proof ""), so the mini-app's poll can
    stop "Waiting for X…" and say what happened.

    A successful proof starts UNCONFIRMED. The authorize URL can be forwarded,
    so whoever authorized on X must confirm, in that same browser, that their
    X account goes to ``telegram_label``. Only a confirmed proof is released
    to the poll or accepted by register. Until then the row holds just the
    sha256 of a one-time confirm token; the raw token exists only in the
    callback's redirect URL.
    """

    __tablename__ = "waitlist_oauth_proofs"

    # autoincrement=False: the Telegram id is always supplied by the caller —
    # without it SQLAlchemy turns a lone integer PK into BIGSERIAL
    telegram_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False
    )
    proof: Mapped[str] = mapped_column(String(2048), nullable=False)
    error: Mapped[str | None] = mapped_column(String(32), default=None)
    created_at: Mapped[datetime] = mapped_column(
        default=utcnow, nullable=False, index=True
    )
    # sha256 hex of the one-time confirm token. Cleared when it's used, and
    # NULL on error rows. Unique: the token alone identifies the row.
    confirm_token_hash: Mapped[str | None] = mapped_column(
        String(64), unique=True, index=True, default=None
    )
    # set when the X account owner confirms in the browser. NULL means the
    # proof is withheld from the poll and refused by register.
    confirmed_at: Mapped[datetime | None] = mapped_column(default=None)
    # copied from the state row: the Telegram account the X account would
    # be linked to, shown on the confirmation page
    telegram_label: Mapped[str] = mapped_column(
        String(120), default="", server_default="", nullable=False
    )
