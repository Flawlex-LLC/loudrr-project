"""One-off: bring users approved BEFORE the 2026-09 launch audit in line with
what approve_entry now writes.

Before that change approve_entry created the User with is_whitelisted=False,
x_verified=False and no x_profiles row — so the mini-app skipped onboarding
and submit_post refused them ("X account not properly linked"). This walks
every approved waitlist entry with a created user and fills in:

  * users.is_whitelisted = True
  * users.x_verified / x_verified_at from the entry (OAuth-proven)
  * x_profiles.x_user_id from the entry when the profile is missing it

Idempotent — safe to run again. Dry-run by default; pass --apply to write.

    ../.venv/Scripts/python.exe -m scripts.backfill_approved_users [--apply]
"""
import argparse
import asyncio

from sqlalchemy import select

from app.core.time_utils import utcnow
from app.db.session import SessionLocal
from app.models.user import User
from app.models.waitlist_entry import WaitlistEntry
from app.models.x_profile import XProfile


async def run(apply: bool) -> None:
    async with SessionLocal() as db:
        rows = (
            await db.execute(
                select(WaitlistEntry, User)
                .join(User, User.id == WaitlistEntry.created_user_id)
                .where(WaitlistEntry.status == "approved")
            )
        ).all()
        touched = 0
        skipped: list[str] = []
        for entry, user in rows:
            changes = []
            if not user.is_whitelisted and not user.is_banned:
                user.is_whitelisted = True
                changes.append("whitelist")
            # The OAuth proof on the entry vouches for ENTRY.x_username only. A
            # user who has since linked a different handle must not have that
            # handle marked verified (or stamped with the entry's X id) — list
            # them for a manual look instead.
            if (user.x_username or "").lower() != (entry.x_username or "").lower():
                skipped.append(
                    f"@{user.x_username or '?'} (entry @{entry.x_username}, tg {user.telegram_id})"
                )
                if changes:
                    touched += 1
                    print(f"@{user.x_username or '?'} ({user.telegram_id}): {', '.join(changes)}")
                continue
            if entry.x_verified and not user.x_verified:
                user.x_verified = True
                user.x_verified_at = user.x_verified_at or utcnow()
                changes.append("x_verified")
            if entry.x_user_id:
                profile = (
                    await db.execute(select(XProfile).where(XProfile.user_id == user.id))
                ).scalar_one_or_none()
                if profile is None:
                    db.add(XProfile(
                        user_id=user.id, x_user_id=entry.x_user_id,
                        username=entry.x_username,
                    ))
                    changes.append("profile+id")
                elif not profile.x_user_id:
                    profile.x_user_id = entry.x_user_id
                    changes.append("profile.id")
            if changes:
                touched += 1
                print(f"@{user.x_username or '?'} ({user.telegram_id}): {', '.join(changes)}")
        print(f"{touched}/{len(rows)} approved users need changes")
        if skipped:
            print(f"{len(skipped)} users now use a different handle than they OAuth'd — NOT verified, review by hand:")
            for line in skipped:
                print("   ", line)
        if apply and touched:
            await db.commit()
            print("applied")
        elif touched:
            await db.rollback()
            print("dry run — re-run with --apply to write")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    asyncio.run(run(ap.parse_args().apply))
