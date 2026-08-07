import uuid
import enum
from datetime import datetime
from decimal import Decimal
from sqlalchemy import String, Text, Numeric, ForeignKey, CheckConstraint, Index, text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import JSONB
from app.core.time_utils import utcnow
from app.db.base import Base


class BatchStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class VerificationBatch(Base):
    """The async claim queue — a batch of engagements awaiting verification (§2.6).

    Created by /session/queue-claim/; a background task runs Phase 1 + Phase 2
    and fills in the results. The frontend polls /claims/history/ for progress.
    """

    __tablename__ = "verification_batches"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE")
    )

    # the engagement UUIDs (as strings) captured into this batch
    engagement_ids: Mapped[list] = mapped_column(
        JSONB, default=list, server_default="[]"
    )

    status: Mapped[str] = mapped_column(
        String(20), default=BatchStatus.PENDING.value,
        server_default=BatchStatus.PENDING.value,
    )

    # filled in after verification completes
    passed: Mapped[int | None] = mapped_column(default=None)
    failed: Mapped[int | None] = mapped_column(default=None)
    credits_awarded: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), default=None)
    message: Mapped[str] = mapped_column(Text, default="", server_default="")

    created_at: Mapped[datetime] = mapped_column(
        default=utcnow, server_default=text("now()")
    )
    completed_at: Mapped[datetime | None] = mapped_column(default=None)

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'processing', 'completed', 'failed')",
            name="batch_status_valid",
        ),
        Index("ix_batches_user_created", "user_id", "created_at"),
        Index("ix_batches_status_created", "status", "created_at"),
    )
