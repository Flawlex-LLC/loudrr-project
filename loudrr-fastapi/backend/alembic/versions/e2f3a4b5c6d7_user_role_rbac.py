"""add users.role for admin RBAC

Adds a `role` column ("" | "admin" | "superadmin") with a CHECK constraint,
backing the require_admin/require_superadmin dependencies and the /api/admin/*
endpoints. Defaults to "" (regular user); admins are bootstrapped from
ADMIN_TELEGRAM_IDS via seed_admins.py.

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
Create Date: 2026-05-28 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e2f3a4b5c6d7"
down_revision: Union[str, Sequence[str], None] = "d1e2f3a4b5c6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("role", sa.String(length=20), server_default="", nullable=False),
    )
    op.create_index("ix_users_role", "users", ["role"])
    op.create_check_constraint(
        "user_role_valid", "users", "role IN ('', 'admin', 'superadmin')"
    )


def downgrade() -> None:
    op.drop_constraint("user_role_valid", "users", type_="check")
    op.drop_index("ix_users_role", table_name="users")
    op.drop_column("users", "role")
