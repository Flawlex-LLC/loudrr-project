import enum
import uuid
from datetime import datetime
from sqlalchemy import (
    String, BigInteger, Boolean, ForeignKey, 
    JSON, Text, CheckConstraint, text,
    )
from sqlalchemy.orm import Mapped, mapped_column
from app.core.time_utils import utcnow
from app.db.base import Base

class WaitlistStatus(str, enum.Enum):
    SUBMITTED = "submitted"
    APPROVED = "approved"
    REJECTED = "rejected"

class Region(str, enum.Enum):
    NORTH_AMERICA = "north_america"
    EUROPE = "europe"
    MIDDLE_EAST = "middle_east"
    SOUTH_ASIA = "south_asia"
    SOUTHEAST_ASIA = "southeast_asia"
    EAST_ASIA = "east_asia"
    AFRICA = "africa"
    LATIN_AMERICA = "latin_america"
    OCEANIA = "oceania"
    CIS_EASTERN_EUROPE = "cis_eastern_europe"

class Niche(str, enum.Enum):
    MEMECOINS = "memecoins"
    GAMEFI = "gamefi"
    TRADING = "trading"
    NFTS = "nfts"
    DEFI = "defi"
    AI_TECH = "ai_tech"
    DAOS = "daos"

class WaitlistEntry(Base):
    __tablename__ = "waitlist_entries"
    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4
    )
    # identity — Telegram-only, no email collection (Ch10, retired 2026-08-07).
    # email column dropped by alembic migration <hash>_drop_waitlist_email.py.
    telegram_id: Mapped[int | None] = mapped_column(
        BigInteger, unique=True, index=True
    )
    telegram_username: Mapped[str] = mapped_column(
        String(100), default="", server_default=""
    )
    # the applicant's Telegram first name — stored here, then copied to
    # User.display_name when the entry is approved (see approve_entry)
    telegram_display_name: Mapped[str] = mapped_column(
        String(100), default="", server_default=""
    )
    x_username: Mapped[str] = mapped_column(String(100), index=True)
    x_link: Mapped[str] = mapped_column(
        String(500), default="", server_default=""
    )
    # OAuth-verified at registration time (added when X OAuth became mandatory
    # step 1 of the waitlist flow). Distinct from x_verified_previously (which
    # tracks legacy re-approval state). Any legacy paste-in-a-URL row remains
    # False; new registrations always have True.
    x_verified: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False,
    )
    # Immutable numeric X user id (from /users/me — string in the API but stable
    # across handle changes). "" for pre-OAuth-flow rows.
    x_user_id: Mapped[str] = mapped_column(
        String(32), default="", server_default="", nullable=False,
    )

    # profile data - region
    # profile data — region / niche validated by CHECK below
    region: Mapped[str] = mapped_column(
    String(30), default="", server_default=""
    )
    niche: Mapped[str] = mapped_column(
    String(20), default="", server_default=""
    )
    other_platforms: Mapped[list] = mapped_column(
    JSON, default=list, server_default="[]"
    )
    # referral — applicant's OWN code, and the one they used (if any)
    referral_code: Mapped[str] = mapped_column(
    String(16), unique=True, index=True
    )
    referrer_id: Mapped[uuid.UUID | None] = mapped_column(
    ForeignKey("users.id", ondelete="SET NULL")
    )
    referral_code_used: Mapped[str] = mapped_column(
    String(16), default="", server_default="", index=True
    )
    total_referrals: Mapped[int] = mapped_column(
        default=0, server_default="0"
    )
    # THE STATE-MACHINE COLUMN — see CheckConstraint below
    status: Mapped[str] = mapped_column(
    String(20),
    default="submitted",
    server_default="submitted",
    index=True,
    )
    rejection_reason: Mapped[str] = mapped_column(
    Text, default="", server_default=""
    )
    # approval bookkeeping
    approved_at: Mapped[datetime | None] = mapped_column()
    approved_by_id: Mapped[uuid.UUID | None] = mapped_column(
    ForeignKey("users.id", ondelete="SET NULL")
    )
    created_user_id: Mapped[uuid.UUID | None] = mapped_column(
    ForeignKey("users.id", ondelete="SET NULL"), unique=True
    )

    # a returning user who got X-verified before getting rejected;
    # on re-approval we skip the X verification step (Ch10)
    x_verified_previously: Mapped[bool] = mapped_column(
    Boolean, default=False, server_default="false"
    )
    created_at: Mapped[datetime] = mapped_column(
    default=utcnow, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
    default=utcnow, server_default=text("now()"),
    onupdate=utcnow,
    )
    __table_args__ = (
    # the state machine — no row may ever hold a value outside the three
    CheckConstraint(
    "status IN ('submitted', 'approved', 'rejected')",
    name="waitlist_status_valid",
    ),
    # region must be empty OR one of the ten allowed values
    CheckConstraint(
    "region = '' OR region IN ("
    "'north_america','europe','middle_east','south_asia',"
    "'southeast_asia','east_asia','africa','latin_america',"
    "'oceania','cis_eastern_europe')",
    name="waitlist_region_valid",
    ),
    # niche must be empty OR one of the seven allowed values
    CheckConstraint(
    "niche = '' OR niche IN ("
    "'memecoins','gamefi','trading','nfts','defi','ai_tech','daos')",
    name="waitlist_niche_valid",
    ),
    )