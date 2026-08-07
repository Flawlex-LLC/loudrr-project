"""drop waitlist_entries.email — Telegram-only signup, no email collected

Revision ID: c5d6e7f8a9b0
Revises: b2c3d4e5f6a7
Create Date: 2026-08-07

Retiring the email field from the waitlist flow. Product decision (2026-08-07):
Loudrr signup is now Telegram-only — telegram_id is the identity, we notify
via the bot, and there's no email service in the codebase to send anything.
Keeping the column would just be dead data drifting from the frontend.

DOWNGRADE preserves the old shape as NULLABLE (not the original NOT NULL
with unique index) so a rollback doesn't require backfilling emails.
"""
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op


revision: str = "c5d6e7f8a9b0"
down_revision: Union[str, Sequence[str], None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop the unique index first (name derived from SQLAlchemy's default
    # convention: ix_<table>_<column>). If the constraint was named differently
    # by an earlier migration, drop_constraint would fail; drop_index with
    # if_exists=True is the safe generic approach.
    op.drop_index("ix_waitlist_entries_email", table_name="waitlist_entries", if_exists=True)
    op.drop_column("waitlist_entries", "email")


def downgrade() -> None:
    # Restore as NULLABLE + not-unique so a rollback works even after real
    # sign-ups have accumulated telegram-only rows with no email to backfill.
    op.add_column(
        "waitlist_entries",
        sa.Column("email", sa.String(length=254), nullable=True),
    )
    op.create_index(
        "ix_waitlist_entries_email",
        "waitlist_entries",
        ["email"],
        unique=False,
    )
