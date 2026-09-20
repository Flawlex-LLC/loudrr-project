"""waitlist X OAuth: browser confirmation before a proof is released

Revision ID: 1b8993ddd938
Revises: d5f7a9b1c3e6
Create Date: 2026-09-17

Closes the forwarded-authorize-link hole. Someone could start "Connect X" in
their own mini-app, send the X authorize URL to a victim, and receive a proof
binding the victim's X account to their Telegram when the victim authorized.
The callback now stores the proof UNCONFIRMED and sends the browser that
authorized on X to a confirmation page. That page shows which Telegram account
started the flow, and only a confirmed proof reaches the poll or register.

* waitlist_oauth_states.telegram_label: who started the flow, e.g. "@alice (Alice B)".
* waitlist_oauth_proofs.telegram_label: copied from the state row by the callback.
* waitlist_oauth_proofs.confirm_token_hash: sha256 hex of the one-time confirm
  token. Unique index; cleared once the token is used.
* waitlist_oauth_proofs.confirmed_at: NULL until the X account owner confirms.

Existing rows: both tables hold only short-lived handoff data with a 10-minute
TTL. A proof row stored before this migration has NULL confirm_token_hash and
NULL confirmed_at. It counts as unconfirmed, and no token exists to confirm it,
so the poll reports it as "expired" and register refuses it. The applicant
presses Connect X again. A state row started before the upgrade gets
telegram_label '', and the callback falls back to "Telegram user <id>".
No backfill is needed.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1b8993ddd938"
down_revision: Union[str, Sequence[str], None] = "d5f7a9b1c3e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "waitlist_oauth_states",
        sa.Column("telegram_label", sa.String(length=120), server_default="", nullable=False),
    )
    op.add_column(
        "waitlist_oauth_proofs",
        sa.Column("telegram_label", sa.String(length=120), server_default="", nullable=False),
    )
    op.add_column(
        "waitlist_oauth_proofs",
        sa.Column("confirm_token_hash", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "waitlist_oauth_proofs",
        sa.Column("confirmed_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        op.f("ix_waitlist_oauth_proofs_confirm_token_hash"),
        "waitlist_oauth_proofs",
        ["confirm_token_hash"],
        unique=True,
    )


def downgrade() -> None:
    """Downgrade schema."""
    # The older code hands any stored proof to the poll. Proofs still waiting
    # for their X account owner's confirmation (possibly a forwarded link)
    # must not turn into released ones, so drop them. They're 10-minute
    # handoff rows, and the applicant simply connects X again.
    op.execute(
        "DELETE FROM waitlist_oauth_proofs WHERE confirmed_at IS NULL AND error IS NULL"
    )
    op.drop_index(
        op.f("ix_waitlist_oauth_proofs_confirm_token_hash"),
        table_name="waitlist_oauth_proofs",
    )
    op.drop_column("waitlist_oauth_proofs", "confirmed_at")
    op.drop_column("waitlist_oauth_proofs", "confirm_token_hash")
    op.drop_column("waitlist_oauth_proofs", "telegram_label")
    op.drop_column("waitlist_oauth_states", "telegram_label")
