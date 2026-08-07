"""XP service — non-spendable, platform-funded sponsored-XP currency.

Mirrors Django's core.services.xp.XPService. Three rules taken verbatim from
the reference docstring (core/services/xp.py:21-31):

  1. XP is NOT spendable — there is no spend()/convert() method.
  2. XP is platform-funded (out of thin air) — it does NOT debit any escrow.
  3. XP is awarded ONLY through these four entrypoints: earn_from_sponsored,
     admin_grant, admin_revoke, award_bonus.

Every write composes inside the caller's transaction: we never call
db.commit() — only db.flush() — so settlement's savepoint (the
`async with db.begin_nested()` block in _settle_passed) owns the atomicity.
If the savepoint rolls back, the XP row, the User counter bump, and the
karma earn() all unwind together.
"""
import logging
import uuid
from decimal import Decimal

from sqlalchemy import select

from app.models.user import User
from app.models.xp_transaction import XPTransaction, XPTransactionType
from app.services.site_settings import get_setting

logger = logging.getLogger(__name__)


def _to_int(amount) -> int:
    """XP counters are integers; coerce a Decimal/str/int input cleanly so
    callers don't need to pre-cast (matches Django, which stores XP as int
    on User but Decimal on the ledger row)."""
    if isinstance(amount, Decimal):
        return int(amount)
    return int(amount)


def _to_decimal(amount) -> Decimal:
    """Ledger amount is Numeric(12,4) — coerce so SQLAlchemy doesn't lose
    precision rounding through float."""
    if isinstance(amount, Decimal):
        return amount
    return Decimal(str(amount))


class XPService:
    """Composable XP writer. Constructed with the caller's session + the
    target User; every operation flushes (so balance_after reflects this
    write) but does NOT commit (the caller owns the transaction)."""

    def __init__(self, db, user: User):
        self.db = db
        self.user = user

    async def _write_row(
        self,
        *,
        type_: XPTransactionType,
        amount: int,
        description: str,
        reference_id: uuid.UUID | None = None,
        reference_type: str = "",
    ) -> XPTransaction:
        """Append one row to xp_transactions. Caller has already bumped the
        relevant User counter — we snapshot that into balance_after."""
        row = XPTransaction(
            user_id=self.user.id,
            type=type_,
            amount=_to_decimal(amount),
            balance_after=_to_decimal(self.user.sponsored_xp),
            reference_id=reference_id,
            reference_type=reference_type,
            description=description,
        )
        self.db.add(row)
        await self.db.flush()
        return row

    async def earn_from_sponsored(
        self,
        *,
        amount,
        post_id: uuid.UUID,
        description: str = "Sponsored engagement reward",
    ) -> XPTransaction | None:
        """Award XP for engaging a sponsored post.

        Bumps current balance, lifetime total, and the engagement counter
        atomically (within the caller's savepoint) and writes one ledger row.
        Returns None (no-op) when amount <= 0 so the caller can pass the raw
        site-setting value without guarding.
        """
        xp = _to_int(amount)
        if xp <= 0:
            return None
        self.user.sponsored_xp += xp
        self.user.total_sponsored_xp_earned += xp
        self.user.sponsored_engagements += 1
        return await self._write_row(
            type_=XPTransactionType.EARNED,
            amount=xp,
            description=description,
            reference_id=post_id,
            reference_type="post",
        )

    async def admin_grant(
        self,
        *,
        amount,
        admin_user: User,
        description: str = "",
    ) -> XPTransaction:
        """Manually grant XP (admin tool — does NOT touch karma)."""
        xp = _to_int(amount)
        if xp <= 0:
            raise ValueError("Amount must be positive")
        self.user.sponsored_xp += xp
        self.user.total_sponsored_xp_earned += xp
        return await self._write_row(
            type_=XPTransactionType.ADMIN_GRANT,
            amount=xp,
            description=description or f"Admin {admin_user.id} granted {xp} XP",
            reference_id=admin_user.id,
            reference_type="admin_user",
        )

    async def admin_revoke(
        self,
        *,
        amount,
        admin_user: User,
        description: str = "",
    ) -> XPTransaction | None:
        """Claw back XP. Clamps to the available balance so the DB-level
        `sponsored_xp >= 0` check is never tripped. Lifetime total is NOT
        decremented (revokes do not rewrite history — same as Django).
        Returns None when there is nothing to take."""
        xp = _to_int(amount)
        if xp <= 0:
            raise ValueError("Amount must be positive")
        # lock the user row so a concurrent earn can't race the clamp
        row = (
            await self.db.execute(
                select(User).where(User.id == self.user.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        available = max(0, row.sponsored_xp)
        taken = min(xp, available)
        if taken <= 0:
            return None
        row.sponsored_xp -= taken
        # mirror back so the caller's reference reflects the new balance
        self.user.sponsored_xp = row.sponsored_xp
        return await self._write_row(
            type_=XPTransactionType.ADMIN_REVOKE,
            amount=-taken,  # negative — a deduction
            description=description or f"Admin {admin_user.id} revoked {taken} XP",
            reference_id=admin_user.id,
            reference_type="admin_user",
        )

    async def award_bonus(
        self,
        *,
        amount,
        reason: str,
        reference_id: uuid.UUID | None = None,
        reference_type: str = "",
    ) -> XPTransaction:
        """Campaign / giveaway bonus — same write shape as earn, different
        ledger type so admins can filter by source."""
        xp = _to_int(amount)
        if xp <= 0:
            raise ValueError("Amount must be positive")
        self.user.sponsored_xp += xp
        self.user.total_sponsored_xp_earned += xp
        return await self._write_row(
            type_=XPTransactionType.BONUS,
            amount=xp,
            description=reason,
            reference_id=reference_id,
            reference_type=reference_type,
        )


async def get_xp_for_sponsored_engagement(db) -> int:
    """Site-setting reader — Django parity (core/services/xp.py:274-276).
    Default 5 matches echo/settings.py:534 so unseeded test DBs still work."""
    return int(await get_setting(db, "SPONSORED_XP_PER_ENGAGEMENT", default=5))
