"""waitlist_entries score columns (fetched at sign-up / on refresh)

Revision ID: b2c4d6e8f0a1
Revises: e8f9a0b1c2d3
Create Date: 2026-09-17

Scores are fetched at exactly two moments — sign-up and the user's
"Refresh score" tap — so the result has to be stored for everything else
(the waitlist card, the admin tables, approval) to read. All three columns
are nullable: NULL score_updated_at means "not fetched yet".
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "b2c4d6e8f0a1"
down_revision: Union[str, Sequence[str], None] = "e8f9a0b1c2d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("waitlist_entries", sa.Column("score", sa.Float(), nullable=True))
    op.add_column(
        "waitlist_entries",
        sa.Column("score_data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column("waitlist_entries", sa.Column("score_updated_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("waitlist_entries", "score_updated_at")
    op.drop_column("waitlist_entries", "score_data")
    op.drop_column("waitlist_entries", "score")
