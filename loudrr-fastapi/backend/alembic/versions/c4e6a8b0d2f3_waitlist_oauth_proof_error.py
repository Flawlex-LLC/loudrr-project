"""waitlist_oauth_proofs.error — tell the mini-app why X OAuth failed

Revision ID: c4e6a8b0d2f3
Revises: b2c4d6e8f0a1
Create Date: 2026-09-17

The OAuth callback runs in the system browser; the mini-app only learns the
outcome by polling GET /waitlist/x-oauth/proof/. Failures (denied on X,
expired state, token/profile errors) used to be visible only in the browser
URL, so the mini-app spun on "Waiting for X…" forever. The callback now
stores the error code in this nullable column (proof "") for the poll to
report once.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4e6a8b0d2f3"
down_revision: Union[str, Sequence[str], None] = "b2c4d6e8f0a1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("waitlist_oauth_proofs", sa.Column("error", sa.String(length=32), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("waitlist_oauth_proofs", "error")
