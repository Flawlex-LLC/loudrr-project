"""add waitlist_entries.telegram_display_name (fixes Ch8 migration omission)

The WaitlistEntry model and the register flow have always used
telegram_display_name, but migration 50d00d7106f2 never created the column —
so on a migrated DB a waitlist insert would fail. This adds it.

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-05-26 07:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c9d0e1f2a3b4"
down_revision: Union[str, Sequence[str], None] = "b8c9d0e1f2a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "waitlist_entries",
        sa.Column("telegram_display_name", sa.String(length=100), server_default="", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("waitlist_entries", "telegram_display_name")
