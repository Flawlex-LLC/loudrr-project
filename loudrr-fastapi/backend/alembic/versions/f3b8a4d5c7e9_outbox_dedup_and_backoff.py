"""outbox: add next_retry_at + idempotency_key for 429 backoff and per-event dedup

Revision ID: f3b8a4d5c7e9
Revises: e2f3a4b5c6d7
Create Date: 2026-06-06

Two new columns on outbox_events that the model (app/models/outbox_event.py)
already defines but no migration had created:

  - next_retry_at (TIMESTAMP, nullable) — set when a 429 from Telegram includes
    a Retry-After header. The drain query filters out rows whose next_retry_at
    is in the future, so a burst of approvals that rate-limits the bot won't
    burn the entire max_retries budget in 3 minutes.

  - idempotency_key (VARCHAR(200), nullable) — set by queue_X helpers that
    want at-most-once delivery semantics. Example: queue_daily_cap_reached
    computes key = f"daily_cap_reached:{user_id}:{date}" so the same user
    hitting the cap 50 times in one day still only gets ONE Telegram message.

  Plus a unique partial index on (event_type, idempotency_key) WHERE
  idempotency_key IS NOT NULL — enforces the dedup at the DB level so two
  concurrent web workers can't both insert the duplicate.

The columns are nullable so existing rows survive untouched; the index is a
WHERE-clause partial so it only constrains rows that explicitly opt in to
dedup.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f3b8a4d5c7e9"
down_revision: Union[str, Sequence[str], None] = "e2f3a4b5c6d7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # next_retry_at — null means "ready to drain now" (the default behavior
    # for rows that haven't hit a 429)
    op.add_column(
        "outbox_events",
        sa.Column("next_retry_at", sa.TIMESTAMP(timezone=False), nullable=True),
    )

    # idempotency_key — null means "no dedup requested" (most events). When
    # set, the unique partial index below enforces at-most-once.
    op.add_column(
        "outbox_events",
        sa.Column("idempotency_key", sa.String(length=200), nullable=True),
    )

    # Partial unique index: only constrains rows that opt in to dedup. Two
    # rows with idempotency_key=NULL coexist freely; two rows with the same
    # (event_type, idempotency_key) non-null pair cannot.
    op.create_index(
        "ix_outbox_event_idempotency_unique",
        "outbox_events",
        ["event_type", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )

    # Plain index on next_retry_at to keep the drain SELECT fast — the query
    # filters WHERE next_retry_at IS NULL OR next_retry_at <= now(), so an
    # index helps when many rows have a future retry time set.
    op.create_index(
        "ix_outbox_event_next_retry_at",
        "outbox_events",
        ["next_retry_at"],
        postgresql_where=sa.text("next_retry_at IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_outbox_event_next_retry_at",
        table_name="outbox_events",
    )
    op.drop_index(
        "ix_outbox_event_idempotency_unique",
        table_name="outbox_events",
    )
    op.drop_column("outbox_events", "idempotency_key")
    op.drop_column("outbox_events", "next_retry_at")
