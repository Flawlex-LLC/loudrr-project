import uuid
from datetime import datetime
from sqlalchemy import String, ForeignKey, Index, text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import JSONB
from app.core.time_utils import utcnow
from app.db.base import Base


class AuditLog(Base):
    """The append-only admin audit trail — who did what (spec §2.11).

    Every privileged action (grant/revoke credits, ban, review verification)
    writes one immutable row here.
    """

    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # the admin who acted (nullable: kept even if the admin is later removed)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    action: Mapped[str] = mapped_column(String(50))
    target_type: Mapped[str] = mapped_column(String(50), default="", server_default="")
    target_id: Mapped[uuid.UUID | None] = mapped_column(default=None)
    detail: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(
        default=utcnow, server_default=text("now()")
    )

    __table_args__ = (Index("ix_audit_logs_created", "created_at"),)
