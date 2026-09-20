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


# ---------------------------------------------------------------------------
# GET /api/admin/users/ — the Users tab + dashboard tier donut read the score
# ---------------------------------------------------------------------------
async def test_search_users_includes_score_and_tier(client, make_user):
    from datetime import datetime

    admin = await make_user(role="admin")
    await make_user(
        x_username="scored_one", tweetscout_score=587.2,
        tweetscout_last_updated=datetime(2026, 9, 1, 12, 0),
    )
    r = await client.get(
        "/api/admin/users/", params={"telegram_id": admin.telegram_id, "q": "scored_one"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    [row] = body["rows"]
    assert row["tweetscout_score"] == 587.2
    assert row["tier"] == "Based"
    assert row["score_updated_at"] == "2026-09-01T12:00:00"


# ===========================================================================
# Search: matching, LIKE escaping, filters, sorting and paging
# ===========================================================================
async def _seed_searchable(make_user):
    """Three users whose handles all contain '_' (like every seeded account)."""
    alice = await make_user(
        telegram_id=900004, telegram_username="seed_alice", x_username="alice_x",
        display_name="Alice Wonderland", referral_code="REFALICE",
        credits=Decimal("10"), total_credits_earned=Decimal("10"),
    )
    bob = await make_user(
        telegram_id=900005, telegram_username="seed_bob", x_username="bob_x",
        display_name="Bob Builder", referral_code="REFBOB",
        credits=Decimal("30"), total_credits_earned=Decimal("30"),
    )
    ghost = await make_user(  # no telegram_username at all — the unidentifiable row
        telegram_id=900006, telegram_username=None, x_username=None,
        display_name="Ghost Account", referral_code="REFGHOST",
    )
    return alice, bob, ghost


async def _search(client, admin, **params):
    return await client.get(
        "/api/admin/users/", params={"telegram_id": admin.telegram_id, **params}
    )


def _rows(r):
    assert r.status_code == 200, r.text
    return r.json()["rows"]


async def test_search_strips_leading_at_and_whitespace(client, make_user):
    """'@seed_alice' and '  seed_alice  ' both used to return 0 rows."""
    admin = await make_user(role="admin")
    alice, _bob, _ghost = await _seed_searchable(make_user)
    for q in ("@seed_alice", "  seed_alice  ", "SEED_ALICE"):
        rows = _rows(await _search(client, admin, q=q))
        assert [row["id"] for row in rows] == [str(alice.id)], f"q={q!r}"


async def test_search_by_telegram_id(client, make_user):
    """The telegram id is printed in the table — it has to be searchable."""
    admin = await make_user(role="admin")
    alice, _bob, _ghost = await _seed_searchable(make_user)
    rows = _rows(await _search(client, admin, q="900004"))
    assert [row["id"] for row in rows] == [str(alice.id)]


async def test_search_by_uuid_prefix(client, make_user):
    """A uuid prefix pasted out of an audit log finds the row."""
    admin = await make_user(role="admin")
    alice, _bob, _ghost = await _seed_searchable(make_user)
    rows = _rows(await _search(client, admin, q=str(alice.id)[:8]))
    assert [row["id"] for row in rows] == [str(alice.id)]


async def test_search_by_referral_code_and_display_name(client, make_user):
    admin = await make_user(role="admin")
    alice, _bob, ghost = await _seed_searchable(make_user)
    assert [r["id"] for r in _rows(await _search(client, admin, q="REFALICE"))] == [str(alice.id)]
    assert [r["id"] for r in _rows(await _search(client, admin, q="wonderland"))] == [str(alice.id)]
    # the row with NO telegram_username is reachable through its display name
    assert [r["id"] for r in _rows(await _search(client, admin, q="ghost"))] == [str(ghost.id)]


async def test_search_escapes_like_metacharacters(client, make_user):
    """'_' and '%' are LIKE wildcards; unescaped they matched every row."""
    admin = await make_user(role="admin", telegram_username="plainadmin",
                            referral_code="ADMCODE")
    alice, bob, _ghost = await _seed_searchable(make_user)

    # a bare '_' matches only fields that literally contain '_'
    ids = {row["id"] for row in _rows(await _search(client, admin, q="_"))}
    assert str(alice.id) in ids and str(bob.id) in ids
    assert str(admin.id) not in ids, "admin has no '_' in any searchable field"

    # '%' matches nothing — no field contains a literal percent sign
    assert _rows(await _search(client, admin, q="%")) == []
    # 'seed_a' is a literal, not 'seed' + any-char + 'a'
    assert [r["id"] for r in _rows(await _search(client, admin, q="seed_a"))] == [str(alice.id)]
    assert _rows(await _search(client, admin, q="seedxa")) == []


async def test_search_returns_total_and_pages(client, make_user):
    admin = await make_user(role="admin")
    await _seed_searchable(make_user)
    body = (await _search(client, admin, limit=2, offset=0, sort="created_at", dir="asc")).json()
    assert body["total"] == 4  # 3 seeded + the admin
    assert len(body["rows"]) == 2
    assert body["limit"] == 2 and body["offset"] == 0

    page2 = (await _search(client, admin, limit=2, offset=2, sort="created_at", dir="asc")).json()
    assert page2["total"] == 4
    first_ids = {row["id"] for row in body["rows"]}
    assert first_ids.isdisjoint({row["id"] for row in page2["rows"]})


async def test_search_sorts_by_credits(client, make_user):
    admin = await make_user(role="admin")
    alice, bob, _ghost = await _seed_searchable(make_user)
    rows = _rows(await _search(client, admin, sort="credits", dir="desc"))
    assert rows[0]["id"] == str(bob.id)    # 30
    assert rows[1]["id"] == str(alice.id)  # 10


async def test_search_rejects_unknown_sort_dir_flag(client, make_user):
    admin = await make_user(role="admin")
    assert (await _search(client, admin, sort="password")).status_code == 422
    assert (await _search(client, admin, dir="sideways")).status_code == 422
    assert (await _search(client, admin, flag="nope")).status_code == 422


async def test_search_flag_filters(client, make_user, db_session):
    admin = await make_user(role="admin")
    alice, bob, ghost = await _seed_searchable(make_user)
    bob.is_banned = True
    alice.is_whitelisted = True
    await db_session.commit()

    assert [r["id"] for r in _rows(await _search(client, admin, flag="banned"))] == [str(bob.id)]

    not_wl = {r["id"] for r in _rows(await _search(client, admin, flag="not_whitelisted"))}
    assert str(alice.id) not in not_wl and str(ghost.id) in not_wl

    assert [r["id"] for r in _rows(await _search(client, admin, flag="admins"))] == [str(admin.id)]

    never = {r["id"] for r in _rows(await _search(client, admin, flag="never_scored"))}
    assert str(alice.id) in never  # nobody in this fixture has ever been scored


async def test_search_row_carries_identity_fallbacks(client, make_user):
    """display_name + telegram_id ship on every row so the UI never renders a
    bare truncated uuid for a user with no telegram handle."""
    admin = await make_user(role="admin")
    _alice, _bob, _ghost = await _seed_searchable(make_user)
    [row] = _rows(await _search(client, admin, q="ghost"))
    assert row["display_name"] == "Ghost Account"
    assert row["telegram_id"] == 900006
    assert row["created_at"]


# ===========================================================================
# Revoke honesty: response + audit row report what was ACTUALLY taken
# ===========================================================================
async def test_revoke_clamps_and_reports_real_amount(client, make_user, db_session):
    admin = await make_user(role="superadmin")
    target = await make_user(
        credits=Decimal("355.35"), total_credits_earned=Decimal("355.35")
    )
    r = await client.post(
        f"/api/admin/users/{target.id}/revoke-credits/",
        params={"telegram_id": admin.telegram_id},
        json={"amount": "400", "reason": "fraud"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["requested"] == 400.0
    assert body["deducted"] == 355.35   # not 400 — apply_penalty clamped
    assert body["clamped"] is True
    assert body["credits"] == 0.0

    log = (
        await db_session.execute(
            select(AuditLog).where(AuditLog.action == "revoke_credits")
        )
    ).scalar_one()
    assert log.detail["requested"] == "400"
    assert Decimal(log.detail["deducted"]) == Decimal("355.35")


async def test_revoke_on_zero_balance_reports_zero(client, make_user):
    """apply_penalty writes NOTHING at a 0 balance — say so instead of lying."""
    admin = await make_user(role="superadmin")
    target = await make_user(credits=Decimal("0"))
    r = await client.post(
        f"/api/admin/users/{target.id}/revoke-credits/",
        params={"telegram_id": admin.telegram_id},
        json={"amount": "50"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["deducted"] == 0.0
    assert r.json()["clamped"] is True


async def test_revoke_self_refused(client, make_user):
    admin = await make_user(role="superadmin", credits=Decimal("100"),
                            total_credits_earned=Decimal("100"))
    r = await client.post(
        f"/api/admin/users/{admin.id}/revoke-credits/",
        params={"telegram_id": admin.telegram_id},
        json={"amount": "10"},
    )
    assert r.status_code == 400
    assert "own account" in r.text


# ===========================================================================
# Grant: bounds, idempotency, description reaching the audit row
# ===========================================================================
async def test_grant_over_numeric_range_is_422_not_500(client, make_user):
    """credits is Numeric(12,4): 1e12 used to explode with an opaque 500."""
    admin = await make_user(role="admin")
    target = await make_user()
    r = await client.post(
        f"/api/admin/users/{target.id}/grant-credits/",
        params={"telegram_id": admin.telegram_id},
        json={"amount": "1000000000000"},
    )
    assert r.status_code == 422


async def test_grant_is_idempotent_on_request_id(client, make_user, db_session):
    admin = await make_user(role="admin")
    target = await make_user()
    payload = {"amount": "25", "description": "promo", "request_id": "req-abc-123"}
    first = await client.post(
        f"/api/admin/users/{target.id}/grant-credits/",
        params={"telegram_id": admin.telegram_id}, json=payload,
    )
    second = await client.post(
        f"/api/admin/users/{target.id}/grant-credits/",
        params={"telegram_id": admin.telegram_id}, json=payload,
    )
    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["granted"] == 25.0
    assert second.json()["granted"] == 0.0
    assert second.json()["duplicate"] is True
    await db_session.refresh(target)
    assert target.credits == Decimal("25")  # granted ONCE


async def test_grant_records_description_in_audit(client, make_user, db_session):
    """The field is labelled 'Description (audit log)' — it must land there."""
    admin = await make_user(role="admin")
    target = await make_user()
    r = await client.post(
        f"/api/admin/users/{target.id}/grant-credits/",
        params={"telegram_id": admin.telegram_id},
        json={"amount": "12", "description": "partnership reward"},
    )
    assert r.status_code == 200, r.text
    log = (
        await db_session.execute(
            select(AuditLog).where(AuditLog.action == "grant_credits")
        )
    ).scalar_one()
    assert log.detail["description"] == "partnership reward"
    assert Decimal(log.detail["granted"]) == Decimal("12")


async def test_grant_counts_toward_earned_so_it_is_spendable(client, make_user, db_session):
    """The modal claims a grant increments total_credits_earned — verify it,
    because that is what makes granted karma actually spendable."""
    admin = await make_user(role="admin")
    target = await make_user()
    await client.post(
        f"/api/admin/users/{target.id}/grant-credits/",
        params={"telegram_id": admin.telegram_id}, json={"amount": "40"},
    )
    await db_session.refresh(target)
    assert target.credits == Decimal("40")
    assert target.total_credits_earned == Decimal("40")
    headroom = min(
        target.credits, target.total_credits_earned - target.total_credits_spent
    )
    assert headroom == Decimal("40")


# ===========================================================================
# Ban / unban: the whitelist round-trip, and self-ban refusal
# ===========================================================================
async def test_unban_restores_whitelist(client, make_user, db_session):
    admin = await make_user(role="admin")
    target = await make_user(is_whitelisted=True)

    ban = await client.post(
        f"/api/admin/users/{target.id}/ban/",
        params={"telegram_id": admin.telegram_id}, json={"reason": "spam"},
    )
    assert ban.json()["is_whitelisted"] is False
    await db_session.refresh(target)
    assert target.is_whitelisted is False

    unban = await client.post(
        f"/api/admin/users/{target.id}/unban/",
        params={"telegram_id": admin.telegram_id},
    )
    assert unban.status_code == 200, unban.text
    assert unban.json()["is_whitelisted"] is True
    await db_session.refresh(target)
    assert target.is_banned is False
    assert target.is_whitelisted is True


async def test_unban_does_not_invent_whitelist(client, make_user):
    """A user who was never whitelisted stays un-whitelisted after unban."""
    admin = await make_user(role="admin")
    target = await make_user(is_whitelisted=False)
    await client.post(
        f"/api/admin/users/{target.id}/ban/",
        params={"telegram_id": admin.telegram_id}, json={"reason": "x"},
    )
    r = await client.post(
        f"/api/admin/users/{target.id}/unban/",
        params={"telegram_id": admin.telegram_id},
    )
    assert r.json()["is_whitelisted"] is False


async def test_ban_records_previous_whitelist_in_audit(client, make_user, db_session):
    admin = await make_user(role="admin")
    target = await make_user(is_whitelisted=True)
    await client.post(
        f"/api/admin/users/{target.id}/ban/",
        params={"telegram_id": admin.telegram_id}, json={"reason": "spam"},
    )
    log = (
        await db_session.execute(
            select(AuditLog).where(AuditLog.action == "ban_user")
        )
    ).scalar_one()
    assert log.detail["was_whitelisted"] is True


async def test_ban_self_refused(client, make_user):
    admin = await make_user(role="admin")
    r = await client.post(
        f"/api/admin/users/{admin.id}/ban/",
        params={"telegram_id": admin.telegram_id}, json={"reason": "oops"},
    )
    assert r.status_code == 400
    assert "own account" in r.text


# ===========================================================================
# POST /api/admin/users/{id}/whitelist/
# ===========================================================================
async def test_whitelist_set_and_clear(client, make_user, db_session):
    admin = await make_user(role="superadmin")
    target = await make_user(is_whitelisted=False)
    on = await client.post(
        f"/api/admin/users/{target.id}/whitelist/",
        params={"telegram_id": admin.telegram_id}, json={"value": True},
    )
    assert on.status_code == 200, on.text
    assert on.json()["is_whitelisted"] is True
    await db_session.refresh(target)
    assert target.is_whitelisted is True

    off = await client.post(
        f"/api/admin/users/{target.id}/whitelist/",
        params={"telegram_id": admin.telegram_id}, json={"value": False},
    )
    assert off.json()["is_whitelisted"] is False

    logs = (
        await db_session.execute(
            select(AuditLog).where(AuditLog.action == "set_whitelist")
            .order_by(AuditLog.created_at.asc())
        )
    ).scalars().all()
    assert logs[0].detail == {"old_value": False, "new_value": True}


async def test_whitelist_requires_superadmin(client, make_user):
    admin = await make_user(role="admin")
    target = await make_user()
    r = await client.post(
        f"/api/admin/users/{target.id}/whitelist/",
        params={"telegram_id": admin.telegram_id}, json={"value": True},
    )
    assert r.status_code == 403


async def test_whitelist_banned_user_conflicts(client, make_user):
    """ban_xor_whitelist would reject the write — answer with a sentence."""
    admin = await make_user(role="superadmin")
    target = await make_user(is_banned=True)
    r = await client.post(
        f"/api/admin/users/{target.id}/whitelist/",
        params={"telegram_id": admin.telegram_id}, json={"value": True},
    )
    assert r.status_code == 409
    assert "Unban" in r.text
