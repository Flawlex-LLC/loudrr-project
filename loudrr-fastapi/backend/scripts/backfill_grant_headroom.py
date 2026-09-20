"""One-off: make karma granted BEFORE the ledger fix spendable again.

`admin_grant` used to raise `credits` without raising `total_credits_earned`,
while `spend` raises `total_credits_spent` and the DB enforces
`total_credits_earned >= total_credits_spent` (users.earned_ge_spent). So a
user whose balance came from an admin grant hit an IntegrityError — a 500 —
the moment they tried to post with it. The service now counts a grant as
karma received, but rows written before that are still short.

This adds each user's historical ADMIN_GRANT total to total_credits_earned,
so their real spendable headroom matches the balance the panel shows them.
(The parallel refund fix needs no backfill: refunds only ever reduce
total_credits_spent from here on, and reducing it can't break the invariant.)

Idempotent by construction: it only tops a user up to
`earned >= spent + (credits - already-earned headroom)`, so a second run finds
nothing to do. Dry-run by default; pass --apply to write.

    ../.venv/Scripts/python.exe -m scripts.backfill_grant_headroom [--apply]
"""
import argparse
import asyncio
from decimal import Decimal

from sqlalchemy import func, select

from app.db.session import SessionLocal
from app.models.transaction import Transaction, TransactionType
from app.models.user import User


async def main(apply: bool) -> None:
    async with SessionLocal() as db:
        granted = (
            select(
                Transaction.user_id.label("user_id"),
                func.sum(Transaction.amount).label("granted"),
            )
            .where(Transaction.type == TransactionType.ADMIN_GRANT)
            .group_by(Transaction.user_id)
            .subquery()
        )
        rows = (
            await db.execute(
                select(User, granted.c.granted).join(granted, granted.c.user_id == User.id)
            )
        ).all()

        fixed, already_fine = [], 0
        for user, grant_total in rows:
            grant_total = Decimal(str(grant_total or 0))
            headroom = user.total_credits_earned - user.total_credits_spent
            # what the user can actually spend today vs what their balance claims
            missing = min(user.credits, grant_total) - headroom
            if missing <= Decimal("0"):
                already_fine += 1
                continue
            fixed.append((user, headroom, missing))
            if apply:
                user.total_credits_earned += missing

        for user, headroom, missing in fixed:
            who = user.telegram_username or user.x_username or str(user.id)[:8]
            print(
                f"  @{who}: credits {user.credits}, spendable was {headroom} "
                f"-> +{missing} earned"
            )
        if apply and fixed:
            await db.commit()
        print(
            f"{'APPLIED' if apply else 'DRY RUN'}: {len(fixed)} user(s) topped up, "
            f"{already_fine} already fine, {len(rows)} with grants in total"
        )
        if not apply and fixed:
            print("re-run with --apply to write")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="write the changes")
    args = parser.parse_args()
    asyncio.run(main(args.apply))
