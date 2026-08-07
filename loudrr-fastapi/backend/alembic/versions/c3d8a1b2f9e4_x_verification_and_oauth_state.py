"""x_verification_requests + x_oauth_states + user X-verify fields (Ch11)

Revision ID: c3d8a1b2f9e4
Revises: 7b1f9c2a4d6e
Create Date: 2026-05-26 01:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c3d8a1b2f9e4"
down_revision: Union[str, Sequence[str], None] = "7b1f9c2a4d6e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # --- new user columns (Ch11) ---
    op.add_column("users", sa.Column("x_verified_at", sa.DateTime(), nullable=True))
    op.add_column(
        "users",
        sa.Column(
            "pending_claimed_x_user_id",
            sa.String(length=50),
            server_default="",
            nullable=False,
        ),
    )

    # --- x_verification_requests (admin review queue) ---
    op.create_table(
        "x_verification_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "submitted_x_username", sa.String(length=50), server_default="", nullable=False
        ),
        sa.Column(
            "claimed_x_username", sa.String(length=50), server_default="", nullable=False
        ),
        sa.Column(
            "claimed_x_user_id", sa.String(length=50), server_default="", nullable=False
        ),
        sa.Column(
            "status", sa.String(length=10), server_default="PENDING", nullable=False
        ),
        sa.Column("admin_notes", sa.Text(), server_default="", nullable=False),
        sa.Column("reviewed_by_id", sa.Uuid(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "status IN ('PENDING', 'APPROVED', 'REJECTED')",
            name="x_verification_status_valid",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reviewed_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_x_verification_requests_user_id"),
        "x_verification_requests", ["user_id"], unique=False,
    )
    op.create_index(
        op.f("ix_x_verification_requests_status"),
        "x_verification_requests", ["status"], unique=False,
    )
    op.create_index(
        "ix_xverif_status_created",
        "x_verification_requests", ["status", "created_at"], unique=False,
    )
    op.create_index(
        "ix_xverif_user_status",
        "x_verification_requests", ["user_id", "status"], unique=False,
    )

    # --- x_oauth_states (short-lived PKCE state) ---
    op.create_table(
        "x_oauth_states",
        sa.Column("state", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("code_verifier", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("state"),
    )
    op.create_index(
        op.f("ix_x_oauth_states_user_id"), "x_oauth_states", ["user_id"], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_x_oauth_states_user_id"), table_name="x_oauth_states")
    op.drop_table("x_oauth_states")

    op.drop_index("ix_xverif_user_status", table_name="x_verification_requests")
    op.drop_index("ix_xverif_status_created", table_name="x_verification_requests")
    op.drop_index(
        op.f("ix_x_verification_requests_status"), table_name="x_verification_requests"
    )
    op.drop_index(
        op.f("ix_x_verification_requests_user_id"), table_name="x_verification_requests"
    )
    op.drop_table("x_verification_requests")

    op.drop_column("users", "pending_claimed_x_user_id")
    op.drop_column("users", "x_verified_at")
