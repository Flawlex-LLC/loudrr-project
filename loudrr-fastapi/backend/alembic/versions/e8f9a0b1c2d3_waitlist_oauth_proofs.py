"""waitlist_oauth_proofs table + partial unique index on waitlist_entries.x_user_id

Revision ID: e8f9a0b1c2d3
Revises: d7e8f9a0b1c2
Create Date: 2026-08-12

Two things:
1. waitlist_oauth_proofs — server-side handoff of the minted OAuth proof,
   keyed by telegram_id. Fixes the Telegram WebView blocker: openLink()
   completes OAuth in the SYSTEM browser, whose sessionStorage the mini-app
   WebView can never see. The callback upserts the proof here; the mini-app
   polls GET /waitlist/x-oauth/proof/ to consume it.
2. Partial unique index on waitlist_entries.x_user_id — one X account can
   back at most one waitlist entry. Partial (x_user_id <> '') so legacy
   pre-OAuth rows with empty x_user_id don't collide.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e8f9a0b1c2d3"
down_revision: Union[str, Sequence[str], None] = "d7e8f9a0b1c2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "waitlist_oauth_proofs",
        sa.Column("telegram_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("proof", sa.String(length=2048), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("telegram_id"),
    )
    op.create_index(
        op.f("ix_waitlist_oauth_proofs_created_at"),
        "waitlist_oauth_proofs",
        ["created_at"],
        unique=False,
    )

    op.create_index(
        "uq_waitlist_entries_x_user_id",
        "waitlist_entries",
        ["x_user_id"],
        unique=True,
        postgresql_where=sa.text("x_user_id <> ''"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "uq_waitlist_entries_x_user_id",
        table_name="waitlist_entries",
    )
    op.drop_index(
        op.f("ix_waitlist_oauth_proofs_created_at"),
        table_name="waitlist_oauth_proofs",
    )
    op.drop_table("waitlist_oauth_proofs")
