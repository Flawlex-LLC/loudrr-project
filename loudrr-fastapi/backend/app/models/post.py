import uuid
import secrets
from datetime import datetime
from decimal import Decimal
from sqlalchemy import (
    String, Text, Numeric, BigInteger, ForeignKey, CheckConstraint, Index, text,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import JSONB
from app.core.time_utils import utcnow
from app.db.base import Base


def generate_redirect_token() -> str:
    return secrets.token_urlsafe(16)


class Post(Base):
    """A post submitted for engagement, holding karma in escrow (spec §2.4).

    A state machine: active → completed | cancelled. Each verified engagement
    decrements `escrow`; when it hits 0 the post auto-completes. The escrow
    lifecycle and the transition methods are built in Ch14 — here the model
    and its DB constraints exist so engagements (Ch12) can reference it.
    """

    __tablename__ = "posts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE")
    )

    # X / Twitter link + cached tweet content (fetched on submission)
    x_link: Mapped[str] = mapped_column(String(500))
    tweet_id: Mapped[str] = mapped_column(String(50), default="", index=True)
    tweet_text: Mapped[str] = mapped_column(Text, default="", server_default="")
    tweet_author_name: Mapped[str] = mapped_column(String(100), default="")
    tweet_author_username: Mapped[str] = mapped_column(String(50), default="")
    tweet_author_avatar: Mapped[str] = mapped_column(String(500), default="")
    tweet_media: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    tweet_created_at: Mapped[datetime | None] = mapped_column(default=None)

    is_sponsored: Mapped[bool] = mapped_column(default=False, server_default="false")

    redirect_token: Mapped[str] = mapped_column(
        String(32), unique=True, index=True, default=generate_redirect_token
    )

    # the money — credits locked for distribution to engagers
    escrow: Mapped[Decimal] = mapped_column(Numeric(12, 4))
    initial_escrow: Mapped[Decimal] = mapped_column(Numeric(12, 4))

    status: Mapped[str] = mapped_column(
        String(20), default="active", server_default="active"
    )
    platform: Mapped[str] = mapped_column(String(20))
    channel_id: Mapped[int | None] = mapped_column(BigInteger, default=None)
    message_id: Mapped[int | None] = mapped_column(BigInteger, default=None)

    created_at: Mapped[datetime] = mapped_column(
        default=utcnow, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        default=utcnow, server_default=text("now()")
    )
    completed_at: Mapped[datetime | None] = mapped_column(default=None)

    __table_args__ = (
        CheckConstraint("escrow >= 0", name="post_escrow_non_negative"),
        CheckConstraint("initial_escrow >= 0", name="post_initial_escrow_non_negative"),
        CheckConstraint("escrow <= initial_escrow", name="post_escrow_cannot_exceed_initial"),
        CheckConstraint(
            "NOT (status = 'completed' AND escrow > 0)", name="post_completed_zero_escrow"
        ),
        CheckConstraint(
            "NOT (status = 'cancelled' AND escrow > 0)", name="post_cancelled_zero_escrow"
        ),
        CheckConstraint(
            "status IN ('active', 'completed', 'cancelled')", name="post_status_valid"
        ),
        Index("ix_posts_status_created", "status", "created_at"),
        Index("ix_posts_user_created", "user_id", "created_at"),
    )

    # ---- computed (not stored) ----
    @property
    def engagement_count(self) -> Decimal:
        """Karma distributed so far = initial − remaining escrow."""
        return self.initial_escrow - self.escrow

    @property
    def engagement_progress(self) -> int:
        """Progress as a 0–100 percentage."""
        if not self.initial_escrow:
            return 100
        return int((float(self.engagement_count) / float(self.initial_escrow)) * 100)
