"""sponsored XP: user counters + xp_transactions ledger

Revision ID: a1b2c3d4e5f6
Revises: f3b8a4d5c7e9
Create Date: 2026-06-06

Ports the Django sponsored-XP schema (core/migrations/0010_add_xp_fields.py)
to FastAPI/SQLAlchemy. Two pieces:

1. Three new INTEGER counters on `users`:
     - sponsored_xp                — current spendable-but-not-really balance
     - total_sponsored_xp_earned   — lifetime (admin_revoke does NOT decrement)
     - sponsored_engagements       — count of sponsored posts the user engaged
   Plus the DB-level guard `sponsored_xp >= 0` so a buggy revoke can never
   corrupt the balance. Matches Django's CheckConstraint at core/models.py:176.

2. New table `xp_transactions` — append-only ledger mirroring the karma
   `transactions` table but in its own namespace (XP and karma never mix per
   the audit). Columns + indexes follow the Django reference one-for-one.

The XP flow ONLY activates when `Post.is_sponsored=True`; existing posts and
users are unaffected. The columns default to 0 with a server_default so
running the migration is safe on a populated database.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "f3b8a4d5c7e9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 1. add the three counters to users ────────────────────────────────
    op.add_column(
        "users",
        sa.Column(
            "sponsored_xp",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "total_sponsored_xp_earned",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "sponsored_engagements",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )
    # DB-level backstop: even buggy / racing code can never push XP below 0.
    op.create_check_constraint(
        "sponsored_xp_non_negative",
        "users",
        "sponsored_xp >= 0",
    )

    # ── 2. create the xp_transactions ledger ──────────────────────────────
    op.create_table(
        "xp_transactions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        # 'earned' | 'admin_grant' | 'admin_revoke' | 'bonus' — VARCHAR
        # (not a PG ENUM type) so adding a new variant doesn't need ALTER TYPE.
        sa.Column("type", sa.String(length=20), nullable=False),
        # Numeric(12,4) matches the Django reference after migration 0017 and
        # matches our `transactions` table's precision so a shared helper
        # could format both ledgers identically.
        sa.Column("amount", sa.Numeric(precision=12, scale=4), nullable=False),
        sa.Column(
            "balance_after",
            sa.Numeric(precision=12, scale=4),
            nullable=False,
        ),
        # Soft links (no FK) — keeps deletions of source rows from blocking.
        sa.Column("reference_id", sa.Uuid(), nullable=True),
        sa.Column(
            "reference_type",
            sa.String(length=50),
            server_default="",
            nullable=False,
        ),
        sa.Column(
            "description",
            sa.Text(),
            server_default="",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    # Standalone single-column indexes so individual lookups stay cheap…
    op.create_index(
        op.f("ix_xp_transactions_user_id"),
        "xp_transactions",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_xp_transactions_type"),
        "xp_transactions",
        ["type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_xp_transactions_created_at"),
        "xp_transactions",
        ["created_at"],
        unique=False,
    )
    # …and the composite that serves the dominant admin-UI query
    # ("show me this user's XP history, newest first").
    op.create_index(
        "ix_xp_transactions_user_created",
        "xp_transactions",
        ["user_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_xp_transactions_user_created", table_name="xp_transactions")
    op.drop_index(
        op.f("ix_xp_transactions_created_at"), table_name="xp_transactions"
    )
    op.drop_index(op.f("ix_xp_transactions_type"), table_name="xp_transactions")
    op.drop_index(op.f("ix_xp_transactions_user_id"), table_name="xp_transactions")
    op.drop_table("xp_transactions")

    op.drop_constraint("sponsored_xp_non_negative", "users", type_="check")
    op.drop_column("users", "sponsored_engagements")
    op.drop_column("users", "total_sponsored_xp_earned")
    op.drop_column("users", "sponsored_xp")
