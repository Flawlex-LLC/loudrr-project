"""posts + engagements tables (Ch12)

Revision ID: d4e5f6a7b8c9
Revises: c3d8a1b2f9e4
Create Date: 2026-05-26 02:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, Sequence[str], None] = "c3d8a1b2f9e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "posts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("x_link", sa.String(length=500), nullable=False),
        sa.Column("tweet_id", sa.String(length=50), server_default="", nullable=False),
        sa.Column("tweet_text", sa.Text(), server_default="", nullable=False),
        sa.Column("tweet_author_name", sa.String(length=100), server_default="", nullable=False),
        sa.Column("tweet_author_username", sa.String(length=50), server_default="", nullable=False),
        sa.Column("tweet_author_avatar", sa.String(length=500), server_default="", nullable=False),
        sa.Column(
            "tweet_media",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column("tweet_created_at", sa.DateTime(), nullable=True),
        sa.Column("is_sponsored", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("redirect_token", sa.String(length=32), nullable=False),
        sa.Column("escrow", sa.Numeric(precision=12, scale=4), nullable=False),
        sa.Column("initial_escrow", sa.Numeric(precision=12, scale=4), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="active", nullable=False),
        sa.Column("platform", sa.String(length=20), nullable=False),
        sa.Column("channel_id", sa.BigInteger(), nullable=True),
        sa.Column("message_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint("escrow >= 0", name="post_escrow_non_negative"),
        sa.CheckConstraint("initial_escrow >= 0", name="post_initial_escrow_non_negative"),
        sa.CheckConstraint("escrow <= initial_escrow", name="post_escrow_cannot_exceed_initial"),
        sa.CheckConstraint("NOT (status = 'completed' AND escrow > 0)", name="post_completed_zero_escrow"),
        sa.CheckConstraint("NOT (status = 'cancelled' AND escrow > 0)", name="post_cancelled_zero_escrow"),
        sa.CheckConstraint("status IN ('active', 'completed', 'cancelled')", name="post_status_valid"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_posts_tweet_id"), "posts", ["tweet_id"], unique=False)
    op.create_index(
        op.f("ix_posts_redirect_token"), "posts", ["redirect_token"], unique=True
    )
    op.create_index("ix_posts_status_created", "posts", ["status", "created_at"], unique=False)
    op.create_index("ix_posts_user_created", "posts", ["user_id", "created_at"], unique=False)

    op.create_table(
        "engagements",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("post_id", sa.Uuid(), nullable=False),
        sa.Column("clicked_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("credit_granted", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("verified", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("like_verified", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("reply_verified", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("verification_data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "NOT (verified = false AND credit_granted = true)",
            name="engagement_credit_requires_verification",
        ),
        sa.ForeignKeyConstraint(["post_id"], ["posts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "post_id", name="unique_user_post_engagement"),
    )
    op.create_index(
        "ix_engagements_user_created", "engagements", ["user_id", "created_at"], unique=False
    )
    op.create_index(
        "ix_engagements_post_created", "engagements", ["post_id", "created_at"], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_engagements_post_created", table_name="engagements")
    op.drop_index("ix_engagements_user_created", table_name="engagements")
    op.drop_table("engagements")

    op.drop_index("ix_posts_user_created", table_name="posts")
    op.drop_index("ix_posts_status_created", table_name="posts")
    op.drop_index(op.f("ix_posts_redirect_token"), table_name="posts")
    op.drop_index(op.f("ix_posts_tweet_id"), table_name="posts")
    op.drop_table("posts")
