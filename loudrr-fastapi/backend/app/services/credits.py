from decimal import Decimal
from sqlalchemy import select
from app.core.time_utils import utcnow
from app.models.user import User
from app.models.transaction import Transaction, TransactionType
from app.services.site_settings import get_setting
import uuid


class InsufficientCreditsError(Exception):
    """the user doesn't have enough credits to spend."""


class DailyCapReachedError(Exception):
    """The user has hit the daily earning cap."""


class CreditService:
    def __init__(self, db, user: User):
        self.db = db
        self.user = user

    async def _daily_headroom(self) -> Decimal:
        """Karma still earnable today, accounting for the midnight reset."""
        cap = Decimal(str(await get_setting(self.db, "DAILY_EARN_CAP")))
        earned = self.user.daily_credits_earned
        if self.user.daily_earned_reset_at.date() < utcnow().date():
            earned = Decimal("0")  # a new day → the counter resets
        return cap - earned

    async def can_earn(self, amount: Decimal) -> bool:
        """Would the full `amount` fit under today's cap? (settlement uses this
        to skip — not partially pay — when the cap can't take the whole award.)"""
        if not isinstance(amount, Decimal):
            amount = Decimal(str(amount))
        return amount <= await self._daily_headroom()

    async def earn(
        self,
        amount: Decimal,
        idempotency_key: str,
        reference_id=None,
        description: str = "",
        *,
        commit: bool = True,
    ) -> Transaction:
        # strict: refuse empty
        if not idempotency_key:
            raise ValueError("idempotency_key is required")
        # the amount is always a decimal
        if not isinstance(amount, Decimal):
            amount = Decimal(str(amount))
        # a zero/negative earn is meaningless and would write a 0-amount ledger
        # row (now blocked by transaction_amount_nonzero) — refuse it up front
        if amount <= Decimal("0"):
            raise ValueError("Amount must be positive!")
        result = await self.db.execute(
            select(Transaction).where(
                Transaction.user_id == self.user.id,
                Transaction.type == TransactionType.EARNED,
                Transaction.idempotency_key == idempotency_key,
            )
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            return existing  # retry - return the original, change nothing
        # defense 1 - lock user row before reading balance
        result = await self.db.execute(
            select(User).where(User.id == self.user.id)
            .with_for_update()
            # populate_existing: overwrite the identity-map copy with the
            # freshly-LOCKED row — without this, the locked read returns the
            # stale cached object and the lock is silently defeated (double-spend)
            .execution_options(populate_existing=True)
        )
        user = result.scalar_one()
        # controlling the daily cap, reset if its new day
        now = utcnow()
        if user.daily_earned_reset_at.date() < now.date():
            user.daily_credits_earned = Decimal("0")
            user.daily_earned_reset_at = now

        # the cap comes from settings store we built.
        daily_cap = Decimal(str(await get_setting(self.db, "DAILY_EARN_CAP")))
        final_amount = amount
        if user.daily_credits_earned + final_amount > daily_cap:
            # trim to the remaining headroom:
            final_amount = daily_cap - user.daily_credits_earned
            if final_amount <= Decimal("0"):
                raise DailyCapReachedError("Daily earning cap reached!")

        # apply the earn to the locked to row
        user.credits += final_amount
        user.total_credits_earned += final_amount
        user.daily_credits_earned += final_amount

        # write immutable audit record
        txn = Transaction(
            user_id=user.id,
            type=TransactionType.EARNED,
            amount=final_amount,
            balance_after=user.credits,
            reference_id=reference_id,
            idempotency_key=idempotency_key,
            description=description or f"Earned {final_amount} credits.",
        )
        self.db.add(txn)
        # commit=False lets settlement (Ch13) call earn() inside one larger
        # atomic transaction — we flush so the row/balance are live for the
        # next idempotency check, but let the caller own the commit.
        if commit:
            await self.db.commit()
        else:
            await self.db.flush()
        return txn

    async def spend(
        self,
        amount: Decimal,
        idempotency_key: str,
        reference_id=None,
        description: str = "",
    ) -> Transaction:
        # strict: refuse empty
        if not idempotency_key:
            raise ValueError("idempotency_key is required")
        if not isinstance(amount, Decimal):
            amount = Decimal(str(amount))
        if amount <= Decimal("0"):
            raise ValueError("Amount must be positive!")
        result = await self.db.execute(
            select(Transaction).where(
                Transaction.user_id == self.user.id,
                Transaction.type == TransactionType.SPENT,
                Transaction.idempotency_key == idempotency_key,
            )
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            return existing

        # defense 1 - lock the user row
        result = await self.db.execute(
            select(User).where(User.id == self.user.id)
            .with_for_update()
            # populate_existing: overwrite the identity-map copy with the
            # freshly-LOCKED row — without this, the locked read returns the
            # stale cached object and the lock is silently defeated (double-spend)
            .execution_options(populate_existing=True)
        )
        user = result.scalar_one()
        # the spend-specific rule: must be able to afford  it!
        if user.credits < amount:
            raise InsufficientCreditsError(f"Have {user.credits}, need {amount}")
        # move the balances
        user.credits -= amount
        user.total_credits_spent += amount
        # txn log
        txn = Transaction(
            user_id=user.id,
            type=TransactionType.SPENT,
            amount=-amount,
            balance_after=user.credits,
            reference_id=reference_id,
            idempotency_key=idempotency_key,
            description=description or f"Spent {amount} credits.",
        )
        self.db.add(txn)
        await self.db.commit()
        return txn

    async def refund(
        self,
        amount: Decimal,
        idempotency_key: str,
        reference_id=None,
        description: str = "",
    ) -> Transaction:
        # strict: refuse empty
        if not idempotency_key:
            raise ValueError("idempotency_key is required")
        if not isinstance(amount, Decimal):
            amount = Decimal(str(amount))
        if amount <= Decimal("0"):
            raise ValueError("Amount must be positive!")

        result = await self.db.execute(
            select(Transaction).where(
                Transaction.user_id == self.user.id,
                Transaction.type == TransactionType.REFUND,
                Transaction.idempotency_key == idempotency_key,
            )
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            return existing

        # defense 1 - lock the user row
        result = await self.db.execute(
            select(User).where(User.id == self.user.id)
            .with_for_update()
            # populate_existing: overwrite the identity-map copy with the
            # freshly-LOCKED row — without this, the locked read returns the
            # stale cached object and the lock is silently defeated (double-spend)
            .execution_options(populate_existing=True)
        )
        user = result.scalar_one()
        # refund dcredits back.
        user.credits += amount
        txn = Transaction(
            user_id=user.id,
            type=TransactionType.REFUND,
            amount=amount,  # positive gain
            balance_after=user.credits,
            reference_id=reference_id,
            idempotency_key=idempotency_key,
            description=description or f"Refunded: {amount} credits.",
        )
        self.db.add(txn)
        await self.db.commit()
        return txn

    async def admin_grant(
        self,
        amount: Decimal,
        admin_id: uuid.UUID,
        idempotency_key: str,
        description: str = "",
    ) -> Transaction:
        # strict: refuse empty
        if not idempotency_key:
            raise ValueError("idempotency_key is required")
        if not isinstance(amount, Decimal):
            amount = Decimal(str(amount))

        if amount <= Decimal("0"):
            raise ValueError("Amount must be positive!")
        # defence 2 - idempotency:
        result = await self.db.execute(
            select(Transaction).where(
                Transaction.user_id == self.user.id,
                Transaction.type == TransactionType.ADMIN_GRANT,
                Transaction.idempotency_key == idempotency_key,
            )
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            return existing

        # defence: lock row
        result = await self.db.execute(
            select(User).where(User.id == self.user.id)
            .with_for_update()
            # populate_existing: overwrite the identity-map copy with the
            # freshly-LOCKED row — without this, the locked read returns the
            # stale cached object and the lock is silently defeated (double-spend)
            .execution_options(populate_existing=True)
        )

        user = result.scalar_one()
        # no daily cap - admin bypass it
        user.credits += amount
        txn = Transaction(
            user_id=user.id,
            type=TransactionType.ADMIN_GRANT,
            amount=amount,
            balance_after=user.credits,
            idempotency_key=idempotency_key,
            description=description or f"Admin {admin_id} granted {amount}",
        )
        self.db.add(txn)
        await self.db.commit()
        return txn

    async def apply_penalty(
        self,
        amount: Decimal,
        admin_id: uuid.UUID,
        idempotency_key: str,
        reference_id=None,
        description: str = "",
    ) -> Transaction | None:
        """Deduct up to `amount`, clamped to the available balance so it can
        never drive credits negative. Returns the ledger row, or None when the
        balance was already empty (nothing to take → no 0-amount row written)."""
        # strict: refuse empty
        if not idempotency_key:
            raise ValueError("idempotency_key is required")
        if not isinstance(amount, Decimal):
            amount = Decimal(str(amount))
        if amount <= Decimal("0"):
            raise ValueError("Amount must be positive!")
        # defense 2 - idempotency (for penalty)
        result = await self.db.execute(
            select(Transaction).where(
                Transaction.user_id == self.user.id,
                Transaction.type == TransactionType.APPLY_PENALTY,
                Transaction.idempotency_key == idempotency_key,
            )
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            return existing

        # defense 1 - lock the user row
        result = await self.db.execute(
            select(User).where(User.id == self.user.id)
            .with_for_update()
            # populate_existing: overwrite the identity-map copy with the
            # freshly-LOCKED row — without this, the locked read returns the
            # stale cached object and the lock is silently defeated (double-spend)
            .execution_options(populate_existing=True)
        )
        user = result.scalar_one()
        # graceful clamp: take only what's there, so the balance floors at 0
        # instead of going negative (the DB credits_non_negative check is the
        # backstop; this keeps the normal path clean and audit-honest).
        available = user.credits if user.credits > Decimal("0") else Decimal("0")
        deducted = min(amount, available)
        if deducted <= Decimal("0"):
            # nothing to take — a 0-amount ledger row is illegal
            # (transaction_amount_nonzero), so record nothing and release the lock
            await self.db.commit()
            return None
        # total_credits_spent is intentionally NOT touched for a penalty
        user.credits -= deducted
        txn = Transaction(
            user_id=user.id,
            type=TransactionType.APPLY_PENALTY,
            amount=-deducted,
            balance_after=user.credits,
            reference_id=reference_id,
            idempotency_key=idempotency_key,
            description=description or f"penalty of {deducted} by {admin_id}.",
        )
        self.db.add(txn)
        await self.db.commit()
        return txn
