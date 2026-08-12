"""waitlist_entries.x_verified/x_user_id + waitlist_oauth_states table

Revision ID: d7e8f9a0b1c2
Revises: c5d6e7f8a9b0
Create Date: 2026-08-11

Backs the "X OAuth is now mandatory step 1 of the waitlist flow" change.
Adds two columns to waitlist_entries — x_verified (True for OAuth entries,
False for any legacy paste rows) and x_user_id (the immutable numeric X id
from /users/me) — and creates a new waitlist_oauth_states table mirroring
x_oauth_states but keyed on telegram_id (pre-signup applicants have no
User row yet).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d7e8f9a0b1c2"
down_revision: Union[str, Sequence[str], None] = "c5d6e7f8a9b0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "waitlist_entries",
        sa.Column(
            "x_verified",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "waitlist_entries",
        sa.Column(
            "x_user_id",
            sa.String(length=32),
            nullable=False,
            server_default="",
        ),
    )

    op.create_table(
        "waitlist_oauth_states",
        sa.Column("state", sa.String(length=64), nullable=False),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("code_verifier", sa.String(length=128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("state"),
    )
    op.create_index(
        op.f("ix_waitlist_oauth_states_telegram_id"),
        "waitlist_oauth_states",
        ["telegram_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_waitlist_oauth_states_expires_at"),
        "waitlist_oauth_states",
        ["expires_at"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        op.f("ix_waitlist_oauth_states_expires_at"),
        table_name="waitlist_oauth_states",
    )
    op.drop_index(
        op.f("ix_waitlist_oauth_states_telegram_id"),
        table_name="waitlist_oauth_states",
    )
    op.drop_table("waitlist_oauth_states")

    op.drop_column("waitlist_entries", "x_user_id")
    op.drop_column("waitlist_entries", "x_verified")
