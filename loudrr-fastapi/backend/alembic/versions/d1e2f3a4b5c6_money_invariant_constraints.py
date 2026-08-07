"""money invariant CheckConstraints (corruption guards)

Adds DB-level guards so a balance can never be corrupted, even by buggy or
racing code:
  users.credits               >= 0
  users.total_credits_earned  >= 0
  users.total_credits_spent   >= 0
  users.daily_credits_earned  >= 0
  transactions.amount         <> 0   (a zero-amount ledger row is meaningless)

This brings the FastAPI schema in line with the Django reference (which has
credits >= 0) and goes further (totals + daily floors, non-zero ledger rows).
The application layer is hardened in tandem (apply_penalty clamps to the
available balance; settlement skips 0-karma awards) so these constraints are a
backstop, never hit in normal operation.

Revision ID: d1e2f3a4b5c6
Revises: c9d0e1f2a3b4
Create Date: 2026-05-27 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "d1e2f3a4b5c6"
down_revision: Union[str, Sequence[str], None] = "c9d0e1f2a3b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_USER_CHECKS = (
    ("credits_non_negative", "credits >= 0"),
    ("total_earned_non_negative", "total_credits_earned >= 0"),
    ("total_spent_non_negative", "total_credits_spent >= 0"),
    ("daily_earned_non_negative", "daily_credits_earned >= 0"),
)


def upgrade() -> None:
    for name, condition in _USER_CHECKS:
        op.create_check_constraint(name, "users", condition)
    op.create_check_constraint("transaction_amount_nonzero", "transactions", "amount <> 0")


def downgrade() -> None:
    op.drop_constraint("transaction_amount_nonzero", "transactions", type_="check")
    for name, _ in _USER_CHECKS:
        op.drop_constraint(name, "users", type_="check")
