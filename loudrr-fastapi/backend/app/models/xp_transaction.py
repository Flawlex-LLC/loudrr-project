"""Append-only XP ledger — mirrors Django's core.XPTransaction.

XP is a separate currency from karma/credits: it is platform-funded (out of
thin air), non-spendable, and earned only from sponsored-post engagements.
Storing it in its own table (rather than reusing `transactions`) preserves the
karma audit trail cleanly and matches the Django reference schema.

Every XPService write produces exactly one row here, with `balance_after`
snapshotted from the freshly-updated User.sponsored_xp counter so the ledger
is self-describing without a join.
"""
import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Enum as SQLAEnum,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time_utils import utcnow
from app.db.base import Base


class XPTransactionType(str, enum.Enum):
    EARNED = "earned"               # from a sponsored-post engagement
    ADMIN_GRANT = "admin_grant"     # admin awarded XP manually
    ADMIN_REVOKE = "admin_revoke"   # admin clawed XP back
    BONUS = "bonus"                 # campaign / giveaway bonus


class XPTransaction(Base):
    __tablename__ = "xp_transactions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # values_callable: store the lowercase enum VALUES so the column matches
    # what Django writes ('earned', not 'EARNED') — same pattern as TransactionType.
    type: Mapped[XPTransactionType] = mapped_column(
        SQLAEnum(
            XPTransactionType,
            name="xp_transaction_type",
            values_callable=lambda e: [m.value for m in e],
        ),
        index=True,
    )

    # Decimal(12,4) matches the Django core.XPTransaction.amount after
    # migration 0017 (and matches our credits ledger precision so the two
    # tables can share helpers if we ever need to).
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 4))
    balance_after: Mapped[Decimal] = mapped_column(Numeric(12, 4))

    # Soft links to whatever caused this row: a post_id for EARNED, a user_id
    # for ADMIN_GRANT/ADMIN_REVOKE, a campaign id for BONUS. Untyped so we
    # don't impose a FK that would block deletion of the source row.
    reference_id: Mapped[uuid.UUID | None] = mapped_column(default=None)
    reference_type: Mapped[str] = mapped_column(
        String(50), default="", server_default=""
    )

    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(
        default=utcnow, server_default=text("now()"), index=True
    )

    __table_args__ = (
        # The hot lookup is "this user's history, newest first" — admin UI
        # paginates by created_at DESC, so the composite index keeps that fast
        # without a sort. Matches Django's Meta.indexes on core.XPTransaction.
        Index(
            "ix_xp_transactions_user_created",
            "user_id",
            "created_at",
        ),
    )
