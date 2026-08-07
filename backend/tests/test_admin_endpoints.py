"""RBAC + /api/admin/* endpoint tests.

Covers the role gate (none/admin/superadmin) and the four operational
endpoint groups: user credit ops, ban/unban, waitlist approve/reject. All
mutations must route through the service layer — these tests verify both the
auth wall and that the side effects (audit_logs, User.is_banned, etc.) land.
"""
from decimal import Decimal

from sqlalchemy import select

from app.models.audit_log import AuditLog
from app.models.user import User
from app.models.waitlist_entry import WaitlistEntry


# ---------------------------------------------------------------------------
# RBAC gate — grant-credits is the simplest admin-only endpoint to probe
# ---------------------------------------------------------------------------
async def test_grant_unauthenticated_401(client, make_user):
    target = await make_user()
    r = await client.post(
        f"/api/admin/users/{target.id}/grant-credits/",
        json={"amount": "10"},
    )
    assert r.status_code == 401


async def test_grant_regular_user_forbidden(client, make_user):
    user = await make_user(role="")
    target = await make_user()
    r = await client.post(
        f"/api/admin/users/{target.id}/grant-credits/",
        params={"telegram_id": user.telegram_id},
        json={"amount": "10"},
    )
    assert r.status_code == 403


async def test_grant_admin_ok(client, make_user, db_session):
    admin = await make_user(role="admin")
    target = await make_user()
    r = await client.post(
        f"/api/admin/users/{target.id}/grant-credits/",
        params={"telegram_id": admin.telegram_id},
        json={"amount": "25", "description": "promo"},
    )
    assert r.status_code == 200
    assert r.json()["credits"] == 25.0
    # audit row written
    log = (
        await db_session.execute(
            select(AuditLog).where(AuditLog.action == "grant_credits")
        )
    ).scalar_one()
    assert log.actor_id == admin.id
    assert log.target_id == target.id


async def test_grant_superadmin_ok(client, make_user):
    """superadmin inherits admin permissions."""
    admin = await make_user(role="superadmin")
    target = await make_user()
    r = await client.post(
        f"/api/admin/users/{target.id}/grant-credits/",
        params={"telegram_id": admin.telegram_id},
        json={"amount": "5"},
    )
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# require_superadmin — revoke is the privileged-only op
# ---------------------------------------------------------------------------
async def test_revoke_admin_forbidden(client, make_user):
    """plain admin cannot revoke — that's superadmin-only."""
    admin = await make_user(role="admin")
    target = await make_user(credits=Decimal("50"), total_credits_earned=Decimal("50"))
    r = await client.post(
        f"/api/admin/users/{target.id}/revoke-credits/",
        params={"telegram_id": admin.telegram_id},
        json={"amount": "10"},
    )
    assert r.status_code == 403


async def test_revoke_superadmin_ok(client, make_user, db_session):
    admin = await make_user(role="superadmin")
    target = await make_user(credits=Decimal("50"), total_credits_earned=Decimal("50"))
    r = await client.post(
        f"/api/admin/users/{target.id}/revoke-credits/",
        params={"telegram_id": admin.telegram_id},
        json={"amount": "10", "reason": "spam"},
    )
    assert r.status_code == 200
    assert r.json()["credits"] == 40.0


# ---------------------------------------------------------------------------
# Ban / unban — admin-level
# ---------------------------------------------------------------------------
async def test_ban_unban_flow(client, make_user, db_session):
    admin = await make_user(role="admin")
    target = await make_user()

    r = await client.post(
        f"/api/admin/users/{target.id}/ban/",
        params={"telegram_id": admin.telegram_id},
        json={"reason": "spam"},
    )
    assert r.status_code == 200
    assert r.json()["is_banned"] is True
    await db_session.refresh(target)
    assert target.is_banned is True

    r = await client.post(
        f"/api/admin/users/{target.id}/unban/",
        params={"telegram_id": admin.telegram_id},
    )
    assert r.status_code == 200
    assert r.json()["is_banned"] is False


async def test_ban_regular_user_forbidden(client, make_user):
    user = await make_user(role="")
    target = await make_user()
    r = await client.post(
        f"/api/admin/users/{target.id}/ban/",
        params={"telegram_id": user.telegram_id},
        json={"reason": "x"},
    )
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# Waitlist approve/reject — service-backed, creates User + outbox event
# ---------------------------------------------------------------------------
async def _make_waitlist_entry(db_session, *, telegram_id: int) -> WaitlistEntry:
    entry = WaitlistEntry(
        telegram_id=telegram_id,
        x_username=f"x{telegram_id}",
        referral_code=f"WL{telegram_id:08X}"[:16],
    )
    db_session.add(entry)
    await db_session.commit()
    return entry


async def test_waitlist_approve_creates_user(client, make_user, db_session):
    admin = await make_user(role="admin")
    entry = await _make_waitlist_entry(db_session, telegram_id=9999001)

    r = await client.post(
        f"/api/admin/waitlist/{entry.id}/approve/",
        params={"telegram_id": admin.telegram_id},
    )
    assert r.status_code == 200
    new_user_id = r.json()["created_user_id"]
    # the new User row exists and matches the entry's telegram_id
    new_user = (
        await db_session.execute(
            select(User).where(User.telegram_id == 9999001)
        )
    ).scalar_one()
    assert str(new_user.id) == new_user_id

    await db_session.refresh(entry)
    assert entry.status == "approved"
    assert entry.approved_by_id == admin.id


async def test_waitlist_reject(client, make_user, db_session):
    admin = await make_user(role="admin")
    entry = await _make_waitlist_entry(db_session, telegram_id=9999002)

    r = await client.post(
        f"/api/admin/waitlist/{entry.id}/reject/",
        params={"telegram_id": admin.telegram_id},
        json={"reason": "bot"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "rejected"
    await db_session.refresh(entry)
    assert entry.rejection_reason == "bot"


async def test_waitlist_approve_regular_user_forbidden(client, make_user, db_session):
    user = await make_user(role="")
    entry = await _make_waitlist_entry(db_session, telegram_id=9999003)
    r = await client.post(
        f"/api/admin/waitlist/{entry.id}/approve/",
        params={"telegram_id": user.telegram_id},
    )
    assert r.status_code == 403
