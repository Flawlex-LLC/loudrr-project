"""posts.quick_replies — Quick Reply drafts written once per post

Revision ID: 5f7b9d1e3a2c
Revises: 3e5a7c9b1d2f
Create Date: 2026-09-26

Quick Reply opens X's reply box with a draft already typed. The drafts are
written by OpenRouter when a post is created (a few variants, so viewers don't
all paste the same line) and stored here with the model that wrote them.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "5f7b9d1e3a2c"
down_revision: Union[str, Sequence[str], None] = "3e5a7c9b1d2f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("posts", sa.Column(
        "quick_replies", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False,
    ))
    op.add_column("posts", sa.Column("quick_replies_model", sa.String(length=100), server_default="", nullable=False))
    op.add_column("posts", sa.Column("quick_replies_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("posts", "quick_replies_at")
    op.drop_column("posts", "quick_replies_model")
    op.drop_column("posts", "quick_replies")
