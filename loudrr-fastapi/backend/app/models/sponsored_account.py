import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, ForeignKey, Numeric, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time_utils import utcnow
from app.db.base import Base


class SponsoredAccount(Base):
    """An X account whose original posts become sponsored raid posts.

    An admin adds the handle; the gateway monitors the account and the worker's
    stream listener (services/sponsor_stream.py) turns every new original post
    (not replies, not retweets) into a Post with is_sponsored=True, funded with
    `karma_per_post` of platform karma.
    """

    __tablename__ = "sponsored_accounts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # lowercase, no "@" — the matching key for stream events
    x_username: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    # X's permanent id (survives @renames); resolved when the admin adds it
    x_user_id: Mapped[str] = mapped_column(String(50), default="", server_default="", index=True)
    display_name: Mapped[str] = mapped_column(String(100), default="", server_default="")
    avatar_url: Mapped[str] = mapped_column(String(500), default="", server_default="")

    is_active: Mapped[bool] = mapped_column(default=True, server_default="true")
    karma_per_post: Mapped[Decimal] = mapped_column(Numeric(12, 4))
    notes: Mapped[str] = mapped_column(Text, default="", server_default="")

    last_tweet_id: Mapped[str] = mapped_column(String(50), default="", server_default="")
    last_post_at: Mapped[datetime | None] = mapped_column(default=None)
    posts_created: Mapped[int] = mapped_column(default=0, server_default="0")

    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    # posts older than this are never imported (no backfill of history);
    # reset when a paused account is resumed
    active_since: Mapped[datetime] = mapped_column(default=utcnow, server_default=text("now()"))
    created_at: Mapped[datetime] = mapped_column(default=utcnow, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, server_default=text("now()"))

    __table_args__ = (
        CheckConstraint("karma_per_post > 0", name="sponsor_karma_per_post_positive"),
    )
