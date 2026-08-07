"""streak system: user fields + invariants (Django parity, core/models.py:78-81)

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-06-06

Adds the three User columns the streak system needs:

  - longest_streak           INTEGER, default 0 (running max of current_streak)
  - last_engagement_date     DATE, nullable (UTC date of last settled engagement)
  - streak_freeze_available  BOOLEAN, default true (parity column — declared but
                             not yet read by runtime code, mirroring Django
                             core/models.py:81)

Plus two CHECK constraints to keep the math honest:

  - current_streak_non_negative   (current_streak >= 0)
  - longest_streak_ge_current     (longest_streak >= current_streak)

The columns default at the DB level so applying this migration on a populated
database is safe — existing users get longest=0, last=NULL, freeze=true and
the next settled engagement starts their streak.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "longest_streak",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "last_engagement_date",
            sa.Date(),
            nullable=True,
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "streak_freeze_available",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "current_streak_non_negative",
        "users",
        "current_streak >= 0",
    )
    op.create_check_constraint(
        "longest_streak_ge_current",
        "users",
        "longest_streak >= current_streak",
    )


def downgrade() -> None:
    op.drop_constraint("longest_streak_ge_current", "users", type_="check")
    op.drop_constraint("current_streak_non_negative", "users", type_="check")
    op.drop_column("users", "streak_freeze_available")
    op.drop_column("users", "last_engagement_date")
    op.drop_column("users", "longest_streak")
