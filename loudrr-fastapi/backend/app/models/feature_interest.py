import uuid
from datetime import datetime
from sqlalchemy import String, ForeignKey, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import JSONB
from app.core.time_utils import utcnow
from app.db.base import Base


class FeatureInterest(Base):
    """A user registering interest in an upcoming feature (spec §2.11).

    Backs the Campaigns tab's "Register Interest". One row per (user, feature).
    """

    __tablename__ = "feature_interests"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE")
    )
    feature: Mapped[str] = mapped_column(String(50), index=True)
    interests: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    created_at: Mapped[datetime] = mapped_column(
        default=utcnow, server_default=text("now()")
    )

    __table_args__ = (
        UniqueConstraint("user_id", "feature", name="unique_user_feature"),
    )
