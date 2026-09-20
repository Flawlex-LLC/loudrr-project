"""sponsored_accounts + posts.sponsor_id — auto-sponsored posts from monitored X accounts

Revision ID: d5f7a9b1c3e6
Revises: c4e6a8b0d2f3
Create Date: 2026-09-17

An admin adds an X handle; the worker's gateway stream turns each new
original post by that account into a sponsored raid post. posts.sponsor_id
links the post back to its account, and the partial unique index guarantees
one sponsored post per tweet no matter how many times it is delivered.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d5f7a9b1c3e6"
down_revision: Union[str, Sequence[str], None] = "c4e6a8b0d2f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "sponsored_accounts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("x_username", sa.String(length=50), nullable=False),
        sa.Column("x_user_id", sa.String(length=50), server_default="", nullable=False),
        sa.Column("display_name", sa.String(length=100), server_default="", nullable=False),
        sa.Column("avatar_url", sa.String(length=500), server_default="", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("karma_per_post", sa.Numeric(precision=12, scale=4), nullable=False),
        sa.Column("notes", sa.Text(), server_default="", nullable=False),
        sa.Column("last_tweet_id", sa.String(length=50), server_default="", nullable=False),
        sa.Column("last_post_at", sa.DateTime(), nullable=True),
        sa.Column("posts_created", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        sa.Column("active_since", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("karma_per_post > 0", name="sponsor_karma_per_post_positive"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sponsored_accounts_x_username", "sponsored_accounts", ["x_username"], unique=True)
    op.create_index("ix_sponsored_accounts_x_user_id", "sponsored_accounts", ["x_user_id"], unique=False)

    op.add_column("posts", sa.Column("sponsor_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "posts_sponsor_id_fkey", "posts", "sponsored_accounts", ["sponsor_id"], ["id"], ondelete="SET NULL",
    )
    op.create_index("ix_posts_sponsor_id", "posts", ["sponsor_id"], unique=False)
    op.create_index(
        "uq_posts_sponsor_tweet", "posts", ["tweet_id"], unique=True,
        postgresql_where=sa.text("platform = 'sponsor'"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("uq_posts_sponsor_tweet", table_name="posts", postgresql_where=sa.text("platform = 'sponsor'"))
    op.drop_index("ix_posts_sponsor_id", table_name="posts")
    op.drop_constraint("posts_sponsor_id_fkey", "posts", type_="foreignkey")
    op.drop_column("posts", "sponsor_id")
    op.drop_index("ix_sponsored_accounts_x_user_id", table_name="sponsored_accounts")
    op.drop_index("ix_sponsored_accounts_x_username", table_name="sponsored_accounts")
    op.drop_table("sponsored_accounts")
