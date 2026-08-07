"""Bootstrap admin RBAC roles from ADMIN_TELEGRAM_IDS.

Idempotent. Reads the comma-separated env var, promotes each matching User row
to role='superadmin', and auto-creates the canonical dev admin (Oxblest,
telegram_id=6451704338, x_username=0xBlest_) if it's listed but missing — so
`python -m scripts.seed_admins` is the one-stop dev bootstrap, matching the
Django reference (loud/management/commands/test_loud.py assumes this user
exists). Does NOT auto-demote anyone; admins added via the SQLAdmin UI are
kept.

Other missing IDs (real admins, not the debug user) are reported but NOT
created — those should come through the waitlist→approve flow.

Run from backend/ with:
    ../.venv/Scripts/python.exe -m scripts.seed_admins
"""
import asyncio
import secrets

from sqlalchemy import select

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.audit_log import AuditLog
from app.models.user import User

# The canonical dev admin. Matches the Django reference everywhere this id
# appears (backend/loud/management/commands/test_loud.py:195, miniapp/views.py:144).
DEBUG_USER_TELEGRAM_ID = 6451704338
DEBUG_USER_TELEGRAM_USERNAME = "Oxblest"
DEBUG_USER_X_USERNAME = "0xBlest_"
DEBUG_USER_DISPLAY_NAME = "Oxblest"


def _parse_ids(raw: str) -> list[int]:
    out: list[int] = []
    for piece in (raw or "").split(","):
        s = piece.strip()
        if not s:
            continue
        try:
            out.append(int(s))
        except ValueError:
            print(f"  WARN: skipping non-integer telegram_id {s!r}")
    return out


async def _create_debug_user(db) -> User:
    """Create the canonical Oxblest dev user as a fully-set-up superadmin."""
    user = User(
        telegram_id=DEBUG_USER_TELEGRAM_ID,
        telegram_username=DEBUG_USER_TELEGRAM_USERNAME,
        x_username=DEBUG_USER_X_USERNAME,
        display_name=DEBUG_USER_DISPLAY_NAME,
        role="superadmin",
        is_whitelisted=True,
        # mark x-verified so the dev user can submit posts / engage without
        # needing to walk the X-OAuth flow on a fresh DB
        x_verified=True,
        # 10-char unique referral code; deterministic-ish so logs are stable
        referral_code=f"OX{secrets.token_hex(4).upper()}",
    )
    db.add(user)
    await db.flush()  # need user.id for the audit row below
    db.add(AuditLog(
        actor_id=user.id,
        action="create_debug_user",
        target_type="user",
        target_id=user.id,
        detail={"source": "seed_admins.py", "username": DEBUG_USER_TELEGRAM_USERNAME},
    ))
    return user


async def seed() -> None:
    ids = _parse_ids(settings.admin_telegram_ids)
    if not ids:
        print("ADMIN_TELEGRAM_IDS is empty — nothing to seed.")
        return

    print(f"Seeding {len(ids)} admin(s) from ADMIN_TELEGRAM_IDS: {ids}")
    async with SessionLocal() as db:
        created, promoted, missing, already = [], [], [], []
        for tg_id in ids:
            user = (
                await db.execute(select(User).where(User.telegram_id == tg_id))
            ).scalar_one_or_none()

            if user is None:
                if tg_id == DEBUG_USER_TELEGRAM_ID:
                    # auto-create the canonical dev user — matches Django assumption
                    user = await _create_debug_user(db)
                    created.append((tg_id, user.id))
                    continue
                missing.append(tg_id)
                continue

            if user.role == "superadmin":
                already.append(tg_id)
                continue
            prior = user.role
            user.role = "superadmin"
            db.add(AuditLog(
                actor_id=user.id,  # self-bootstrap
                action="seed_admin_role",
                target_type="user",
                target_id=user.id,
                detail={"from": prior, "to": "superadmin", "source": "ADMIN_TELEGRAM_IDS"},
            ))
            promoted.append((tg_id, user.id))

        await db.commit()

        # report users who currently hold an admin role but are NOT in the env
        # list — useful operational signal; do NOT auto-demote
        rows = (
            await db.execute(
                select(User.telegram_id, User.role).where(User.role.in_(("admin", "superadmin")))
            )
        ).all()
        unmanaged = [(tg, r) for tg, r in rows if tg not in ids]

    if created:
        print(f"  created: {len(created)}")
        for tg, uid in created:
            print(f"    + telegram_id={tg} (@{DEBUG_USER_TELEGRAM_USERNAME}) as superadmin (user_id={uid})")
    print(f"  promoted: {len(promoted)}")
    for tg, uid in promoted:
        print(f"    + telegram_id={tg} → superadmin (user_id={uid})")
    if already:
        print(f"  already superadmin: {already}")
    if missing:
        print(f"  no User row for telegram_id (not auto-created): {missing}")
        print("    (these should come through the waitlist→approve flow)")
    if unmanaged:
        print(f"  NOTE: {len(unmanaged)} user(s) hold admin/superadmin but are NOT")
        print(f"        in ADMIN_TELEGRAM_IDS (kept as-is): {unmanaged}")


if __name__ == "__main__":
    asyncio.run(seed())
