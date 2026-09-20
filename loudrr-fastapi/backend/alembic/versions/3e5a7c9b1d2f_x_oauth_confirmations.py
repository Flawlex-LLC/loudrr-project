"""x_oauth_confirmations — the main-app X login waits for a browser confirm

Revision ID: 3e5a7c9b1d2f
Revises: 1b8993ddd938
Create Date: 2026-09-18

The post-approval Connect X flow applied whatever X proved straight to the
user who STARTED it. Its authorize link can be forwarded, so a victim who
authorized a forwarded link handed their X identity (and, when it matched the
attacker's self-declared handle, a verified badge) to the attacker's account.
The callback now stores the proven identity here and renders a page naming
both accounts; only an explicit confirm applies it.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "3e5a7c9b1d2f"
down_revision: Union[str, Sequence[str], None] = "1b8993ddd938"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "x_oauth_confirmations",
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("x_username", sa.String(length=50), nullable=False),
        sa.Column("x_user_id", sa.String(length=50), nullable=False),
        sa.Column("telegram_label", sa.String(length=120), server_default="", nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("token_hash"),
    )
    op.create_index("ix_x_oauth_confirmations_user_id", "x_oauth_confirmations", ["user_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_x_oauth_confirmations_user_id", table_name="x_oauth_confirmations")
    op.drop_table("x_oauth_confirmations")
