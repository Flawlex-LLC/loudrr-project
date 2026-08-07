"""x_profiles table + user tier/verification fields (Ch10)

Revision ID: 7b1f9c2a4d6e
Revises: 50d00d7106f2
Create Date: 2026-05-26 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "7b1f9c2a4d6e"
down_revision: Union[str, Sequence[str], None] = "50d00d7106f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # --- new user columns (Ch10) ---
    op.add_column(
        "users", sa.Column("tweetscout_last_updated", sa.DateTime(), nullable=True)
    )
    op.add_column(
        "users",
        sa.Column(
            "pending_claimed_x_username",
            sa.String(length=50),
            server_default="",
            nullable=False,
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "honesty_score", sa.Integer(), server_default="50", nullable=False
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "loud_access", sa.Boolean(), server_default="false", nullable=False
        ),
    )
    op.create_index(
        op.f("ix_users_loud_access"), "users", ["loud_access"], unique=False
    )
    op.create_check_constraint(
        "honesty_score_range", "users", "honesty_score >= 0 AND honesty_score <= 50"
    )

    # --- x_profiles (TweetScout cache, OneToOne with users) ---
    op.create_table(
        "x_profiles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("x_user_id", sa.String(length=50), server_default="", nullable=False),
        sa.Column("username", sa.String(length=50), server_default="", nullable=False),
        sa.Column(
            "display_name", sa.String(length=100), server_default="", nullable=False
        ),
        sa.Column("bio", sa.Text(), server_default="", nullable=False),
        sa.Column("followers_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("following_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("tweets_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("score", sa.Float(), server_default="0", nullable=False),
        sa.Column("avatar_url", sa.String(length=500), server_default="", nullable=False),
        sa.Column("banner_url", sa.String(length=500), server_default="", nullable=False),
        sa.Column("is_verified", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("can_dm", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("x_created_at", sa.Date(), nullable=True),
        sa.Column(
            "raw_tweetscout_data",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column(
            "fetched_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    # OneToOne: the model declares user_id as unique + indexed, which is a
    # single UNIQUE index (not a separate unique constraint).
    op.create_index(
        op.f("ix_x_profiles_user_id"), "x_profiles", ["user_id"], unique=True
    )
    op.create_index(
        op.f("ix_x_profiles_x_user_id"), "x_profiles", ["x_user_id"], unique=False
    )
    op.create_index(
        op.f("ix_x_profiles_username"), "x_profiles", ["username"], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_x_profiles_username"), table_name="x_profiles")
    op.drop_index(op.f("ix_x_profiles_x_user_id"), table_name="x_profiles")
    op.drop_index(op.f("ix_x_profiles_user_id"), table_name="x_profiles")
    op.drop_table("x_profiles")

    op.drop_constraint("honesty_score_range", "users", type_="check")
    op.drop_index(op.f("ix_users_loud_access"), table_name="users")
    op.drop_column("users", "loud_access")
    op.drop_column("users", "honesty_score")
    op.drop_column("users", "pending_claimed_x_username")
    op.drop_column("users", "tweetscout_last_updated")
