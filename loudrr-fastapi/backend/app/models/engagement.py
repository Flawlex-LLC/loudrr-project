import uuid
from datetime import datetime
from sqlalchemy import (
    ForeignKey, CheckConstraint, UniqueConstraint, Index, text,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import JSONB
from app.core.time_utils import utcnow
from app.db.base import Base


class Engagement(Base):
    """One user engaging with one post (spec §2.5).

    Created (verified=False, credit_granted=False) when the user clicks a post
    in a session. Credit is awarded later, at claim time, by the two-phase
    verification engine (Ch13). One engagement per user per post.
    """

    __tablename__ = "engagements"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE")
    )
    post_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("posts.id", ondelete="CASCADE")
    )

    clicked_at: Mapped[datetime] = mapped_column(
        default=utcnow, server_default=text("now()")
    )
    credit_granted: Mapped[bool] = mapped_column(default=False, server_default="false")

    # verification (set by the Ch13 claim engine)
    verified: Mapped[bool] = mapped_column(default=False, server_default="false")
    like_verified: Mapped[bool] = mapped_column(default=False, server_default="false")
    reply_verified: Mapped[bool] = mapped_column(default=False, server_default="false")
    verification_data: Mapped[dict | None] = mapped_column(JSONB, default=None)

    created_at: Mapped[datetime] = mapped_column(
        default=utcnow, server_default=text("now()")
    )

    __table_args__ = (
        UniqueConstraint("user_id", "post_id", name="unique_user_post_engagement"),
        # can't grant credit without verification
        CheckConstraint(
            "NOT (verified = false AND credit_granted = true)",
            name="engagement_credit_requires_verification",
        ),
        Index("ix_engagements_user_created", "user_id", "created_at"),
        Index("ix_engagements_post_created", "post_id", "created_at"),
    )
