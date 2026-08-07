import uuid
from datetime import datetime, date
from sqlalchemy import String, Text, ForeignKey, text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import JSONB
from app.core.time_utils import utcnow
from app.db.base import Base


class XProfile(Base):
    """A cached TweetScout profile for a user (spec §2.8).

    One row per user (OneToOne). This table IS the TweetScout cache — once
    fetched, repeat reads come from here instead of paying for another API
    call. Fetched on link-X / onboarding / approval.
    """

    __tablename__ = "x_profiles"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # OneToOne — unique FK so a user has at most one cached profile
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True
    )

    # identity (from TweetScout /info)
    x_user_id: Mapped[str] = mapped_column(String(50), default="", index=True)
    username: Mapped[str] = mapped_column(String(50), default="", index=True)
    display_name: Mapped[str] = mapped_column(String(100), default="")
    bio: Mapped[str] = mapped_column(Text, default="", server_default="")

    # metrics
    followers_count: Mapped[int] = mapped_column(default=0, server_default="0")
    following_count: Mapped[int] = mapped_column(default=0, server_default="0")
    tweets_count: Mapped[int] = mapped_column(default=0, server_default="0")

    # the clout score that drives the tier system
    score: Mapped[float] = mapped_column(default=0.0, server_default="0")

    # profile assets
    avatar_url: Mapped[str] = mapped_column(String(500), default="")
    banner_url: Mapped[str] = mapped_column(String(500), default="")

    # account status
    is_verified: Mapped[bool] = mapped_column(default=False, server_default="false")
    can_dm: Mapped[bool] = mapped_column(default=False, server_default="false")
    x_created_at: Mapped[date | None] = mapped_column(default=None)

    # the full TweetScout payload, kept for future-proofing
    raw_tweetscout_data: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default="{}"
    )

    fetched_at: Mapped[datetime] = mapped_column(
        default=utcnow, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        default=utcnow, server_default=text("now()")
    )
