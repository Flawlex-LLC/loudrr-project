import uuid
import enum
from datetime import datetime
from sqlalchemy import String, Text, ForeignKey, Index, CheckConstraint, text
from sqlalchemy.orm import Mapped, mapped_column
from app.core.time_utils import utcnow
from app.db.base import Base


class XVerificationStatus(str, enum.Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class XVerificationRequest(Base):
    """Admin review queue for an X-OAuth mismatch (spec §2.9).

    Created when a user connects an X account whose handle differs from the
    one they submitted, and then confirms "yes, that's mine". An admin later
    approves (adopt the claimed handle, mark verified) or rejects.
    """

    __tablename__ = "x_verification_requests"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )

    # the handle the user originally submitted on the waitlist
    submitted_x_username: Mapped[str] = mapped_column(String(50), default="")
    # the handle returned by X OAuth (the one being verified)
    claimed_x_username: Mapped[str] = mapped_column(String(50), default="")
    # the numeric X user id from OAuth — the source of truth
    claimed_x_user_id: Mapped[str] = mapped_column(String(50), default="")

    # stored as the enum's VALUE ("PENDING", …)
    status: Mapped[str] = mapped_column(
        String(10), default=XVerificationStatus.PENDING.value,
        server_default=XVerificationStatus.PENDING.value, index=True,
    )

    admin_notes: Mapped[str] = mapped_column(Text, default="", server_default="")
    reviewed_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(default=None)

    created_at: Mapped[datetime] = mapped_column(
        default=utcnow, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        default=utcnow, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'APPROVED', 'REJECTED')",
            name="x_verification_status_valid",
        ),
        Index("ix_xverif_status_created", "status", "created_at"),
        Index("ix_xverif_user_status", "user_id", "status"),
    )
