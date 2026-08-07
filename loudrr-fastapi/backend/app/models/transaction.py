import uuid
from datetime import datetime
from decimal import Decimal
from sqlalchemy import String, Numeric, ForeignKey, UniqueConstraint, CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.core.time_utils import utcnow
from app.db.base import Base
import enum
from sqlalchemy import Enum as SQLAEnum


class TransactionType(str, enum.Enum):
    EARNED = "earned"
    SPENT = "spent"
    REFUND = "refund"
    ADMIN_GRANT = "admin_grant"
    APPLY_PENALTY = "apply_penalty"


class Transaction(Base):
    __tablename__ = "transactions"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    # foreign key from users table for UserID
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)

    # earned, spent, refund, admin_grant, apply_penalty
    # values_callable tells SQLAlchemy to store the lowercase VALUES
    # ('earned', etc.) instead of the uppercase NAMES ('EARNED', etc.)
    type: Mapped[TransactionType] = mapped_column(
        SQLAEnum(TransactionType, values_callable=lambda e: [m.value for m in e])
    )

    # positive, gain. negative, a loss. decimals only.
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 4))
    balance_after: Mapped[Decimal] = mapped_column(Numeric(12, 4))

    # reference what caused this - engagement or postID
    reference_id: Mapped[uuid.UUID | None] = mapped_column()
    reference_type: Mapped[str] = mapped_column(String(50), default="")

    # idempotency, duplicate prevention
    idempotency_key: Mapped[str] = mapped_column(String(64), index=True)
    description: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)

    __table_args__ = (
        # no two transactions share a (user, type, key)
        UniqueConstraint(
            "user_id",
            "type",
            "idempotency_key",
            name="transaction_idempotency_unique",
        ),
        # a zero-amount ledger row is meaningless — refuse it (matches Django).
        # services must skip a no-op rather than write a 0 here.
        CheckConstraint("amount <> 0", name="transaction_amount_nonzero"),
    )
