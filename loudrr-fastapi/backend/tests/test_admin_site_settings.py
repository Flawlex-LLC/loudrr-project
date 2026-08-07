"""Tests for the 3 admin tunables/metrics endpoints:

* GET  /api/admin/site-settings/       — list every known setting (admin)
* PUT  /api/admin/site-settings/{key}  — upsert one setting (superadmin)
* GET  /api/admin/stats/               — dashboard metrics (admin)
* GET  /api/admin/stats/timeseries     — per-day buckets for one metric (admin)

These match the style of test_admin_endpoints.py: ``?telegram_id=`` debug
bypass for auth, Decimal for credit amounts, and assertions on persisted
side effects (SiteSetting row, AuditLog row) where relevant.
"""
import uuid
from decimal import Decimal

from sqlalchemy import select

from app.models.audit_log import AuditLog
from app.models.site_setting import SiteSetting
from app.models.transaction import Transaction, TransactionType


# ---------------------------------------------------------------------------
# GET /api/admin/site-settings/
# ---------------------------------------------------------------------------
async def test_site_settings_list_admin_ok(client, make_user):
    """Admin can list site settings, response shape matches ALL_GROUPS."""
    admin = await make_user(role="admin")
    r = await client.get(
        "/api/admin/site-settings/",
        params={"telegram_id": admin.telegram_id},
    )
    assert r.status_code == 200
    data = r.json()
    assert "groups" in data
    assert isinstance(data["groups"], list)
    assert len(data["groups"]) >= 1

    # collect every known key across all groups; POST_COST must be present
    all_keys = {s["key"] for g in data["groups"] for s in g["settings"]}
    assert "POST_COST" in all_keys
    assert "DAILY_EARN_CAP" in all_keys
    assert "TIER_NORMIE_THRESHOLD" in all_keys

    # first group has the expected shape: name, description, settings[]
    g0 = data["groups"][0]
    assert "name" in g0
    assert "description" in g0
    assert isinstance(g0["settings"], list)
    s0 = g0["settings"][0]
    for field in ("key", "value", "default", "data_type", "description", "live", "persisted"):
        assert field in s0, f"missing field {field!r} on setting payload"


async def test_site_settings_list_unauthenticated_401(client):
    r = await client.get("/api/admin/site-settings/")
    assert r.status_code == 401


async def test_site_settings_list_regular_user_403(client, make_user):
    user = await make_user(role="")
    r = await client.get(
        "/api/admin/site-settings/",
        params={"telegram_id": user.telegram_id},
    )
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# PUT /api/admin/site-settings/{key}
# ---------------------------------------------------------------------------
async def test_site_settings_update_superadmin_ok(client, make_user, db_session):
    """Superadmin updates POST_COST: SiteSetting row written + audit_log row."""
    admin = await make_user(role="superadmin")
    r = await client.put(
        "/api/admin/site-settings/POST_COST",
        params={"telegram_id": admin.telegram_id},
        json={"value": "100"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["key"] == "POST_COST"
    assert body["value"] == "100"
    assert body["data_type"] == "int"

    # SiteSetting row is persisted with the new value
    row = (
        await db_session.execute(
            select(SiteSetting).where(SiteSetting.key == "POST_COST")
        )
    ).scalar_one()
    assert row.value == "100"
    assert row.data_type == "int"

    # AuditLog row written with detail.new_value
    log = (
        await db_session.execute(
            select(AuditLog).where(AuditLog.action == "update_site_setting")
        )
    ).scalar_one()
    assert log.actor_id == admin.id
    assert log.detail["key"] == "POST_COST"
    assert log.detail["new_value"] == "100"


async def test_site_settings_update_admin_forbidden(client, make_user):
    """Plain admin cannot tune money math — that's superadmin-only."""
    admin = await make_user(role="admin")
    r = await client.put(
        "/api/admin/site-settings/POST_COST",
        params={"telegram_id": admin.telegram_id},
        json={"value": "100"},
    )
    assert r.status_code == 403


async def test_site_settings_update_unknown_key_404(client, make_user):
    admin = await make_user(role="superadmin")
    r = await client.put(
        "/api/admin/site-settings/NOT_A_REAL_KEY",
        params={"telegram_id": admin.telegram_id},
        json={"value": "whatever"},
    )
    assert r.status_code == 404


async def test_site_settings_update_bad_type_422(client, make_user):
    """POST_COST is an int; non-int value must 422."""
    admin = await make_user(role="superadmin")
    r = await client.put(
        "/api/admin/site-settings/POST_COST",
        params={"telegram_id": admin.telegram_id},
        json={"value": "not-an-int"},
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Streak port: STREAK_* settings are now live=True, admin editor round-trips.
# ---------------------------------------------------------------------------
async def test_streak_bonus_setting_round_trips(client, make_user, db_session):
    """A superadmin can edit STREAK_7_DAY_BONUS and the new value is persisted —
    proves the live=True flip surfaces the setting through the admin editor."""
    admin = await make_user(role="superadmin")
    r = await client.put(
        "/api/admin/site-settings/STREAK_7_DAY_BONUS",
        params={"telegram_id": admin.telegram_id},
        json={"value": "12"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["key"] == "STREAK_7_DAY_BONUS"
    assert body["value"] == "12"
    row = (
        await db_session.execute(
            select(SiteSetting).where(SiteSetting.key == "STREAK_7_DAY_BONUS")
        )
    ).scalar_one()
    assert row.value == "12"


async def test_streak_multiplier_setting_round_trips(client, make_user, db_session):
    """STREAK_30_DAY_MULTIPLIER is a decimal — the editor accepts a decimal
    string and persists it. Settings cache is cleared on every PUT, so the
    next karma_for call will see the new value."""
    admin = await make_user(role="superadmin")
    r = await client.put(
        "/api/admin/site-settings/STREAK_30_DAY_MULTIPLIER",
        params={"telegram_id": admin.telegram_id},
        json={"value": "2.0"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["data_type"] == "decimal"
    row = (
        await db_session.execute(
            select(SiteSetting).where(
                SiteSetting.key == "STREAK_30_DAY_MULTIPLIER"
            )
        )
    ).scalar_one()
    assert row.value == "2.0"


async def test_streak_settings_in_list_response(client, make_user):
    """The list endpoint surfaces all 6 STREAK_* keys with live=True now that
    the port wires them — exposes them in the admin UI Streak group."""
    admin = await make_user(role="admin")
    r = await client.get(
        "/api/admin/site-settings/",
        params={"telegram_id": admin.telegram_id},
    )
    assert r.status_code == 200
    data = r.json()
    all_settings = {
        s["key"]: s for g in data["groups"] for s in g["settings"]
    }
    for key in (
        "STREAK_7_DAY_BONUS", "STREAK_7_DAY_MULTIPLIER",
        "STREAK_14_DAY_BONUS", "STREAK_14_DAY_MULTIPLIER",
        "STREAK_30_DAY_BONUS", "STREAK_30_DAY_MULTIPLIER",
    ):
        assert key in all_settings, f"missing streak key {key!r}"
        assert all_settings[key]["live"] is True


# ---------------------------------------------------------------------------
# GET /api/admin/stats/
# ---------------------------------------------------------------------------
async def test_stats_admin_ok(client, make_user):
    """Admin gets a dashboard payload with all the expected top-level keys."""
    admin = await make_user(role="admin")
    r = await client.get(
        "/api/admin/stats/",
        params={"telegram_id": admin.telegram_id},
    )
    assert r.status_code == 200
    data = r.json()
    for top in ("users", "credits", "posts", "engagements", "queues", "recent_audit"):
        assert top in data, f"missing top-level key {top!r}"

    # users: total + by_role + flag counts
    for k in ("total", "by_role", "banned", "whitelisted", "x_verified", "new_this_week"):
        assert k in data["users"]
    assert isinstance(data["users"]["by_role"], dict)
    for role_key in ("regular", "admin", "superadmin"):
        assert role_key in data["users"]["by_role"]

    # credits: three sums
    for k in ("in_circulation", "total_earned", "total_spent"):
        assert k in data["credits"]

    # posts: status counts + escrow
    for k in ("active", "completed", "cancelled", "total_escrow_active"):
        assert k in data["posts"]

    # engagements: three windowed counts
    for k in ("total", "today", "this_week"):
        assert k in data["engagements"]

    # queues: three backlog counts
    for k in ("pending_waitlist", "pending_x_verifications", "pending_batches"):
        assert k in data["queues"]

    # recent_audit: list of dicts
    assert isinstance(data["recent_audit"], list)


async def test_stats_unauthenticated_401(client):
    r = await client.get("/api/admin/stats/")
    assert r.status_code == 401


async def test_stats_regular_user_403(client, make_user):
    user = await make_user(role="")
    r = await client.get(
        "/api/admin/stats/",
        params={"telegram_id": user.telegram_id},
    )
    assert r.status_code == 403


async def test_stats_with_data(client, make_user):
    """Seed a few users with credits, expect totals to reflect them."""
    admin = await make_user(role="admin")  # 1 admin user
    # three regular users with known credit balances
    u1 = await make_user(credits=Decimal("20"), total_credits_earned=Decimal("20"))
    u2 = await make_user(credits=Decimal("30"), total_credits_earned=Decimal("30"))
    u3 = await make_user(credits=Decimal("50"), total_credits_earned=Decimal("50"))

    r = await client.get(
        "/api/admin/stats/",
        params={"telegram_id": admin.telegram_id},
    )
    assert r.status_code == 200
    data = r.json()

    # at least the four users we just made
    assert data["users"]["total"] >= 4
    # by_role rolls them up correctly
    assert data["users"]["by_role"]["admin"] >= 1
    assert data["users"]["by_role"]["regular"] >= 3

    # in_circulation is the sum of every user's credits; ours add to 100
    # (the admin user has 0 by default) — assert >= in case of seeded rows
    expected_min = float(u1.credits + u2.credits + u3.credits)
    assert data["credits"]["in_circulation"] >= expected_min
    assert data["credits"]["total_earned"] >= expected_min


# ---------------------------------------------------------------------------
# GET /api/admin/stats/timeseries
# ---------------------------------------------------------------------------
async def test_timeseries_karma_admin_ok(client, make_user):
    """Admin can call timeseries for karma_earned; payload shape checks out."""
    admin = await make_user(role="admin")
    r = await client.get(
        "/api/admin/stats/timeseries",
        params={"telegram_id": admin.telegram_id, "metric": "karma_earned", "days": 30},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["metric"] == "karma_earned"
    assert data["days"] == 30
    assert isinstance(data["points"], list)
    assert len(data["points"]) == 30
    for p in data["points"]:
        assert "date" in p and "value" in p
    assert "total" in data
    # delta_pct is either a number or None
    assert data["delta_pct"] is None or isinstance(data["delta_pct"], (int, float))


async def test_timeseries_engagements_admin_ok(client, make_user):
    admin = await make_user(role="admin")
    r = await client.get(
        "/api/admin/stats/timeseries",
        params={"telegram_id": admin.telegram_id, "metric": "engagements", "days": 14},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["metric"] == "engagements"
    assert data["days"] == 14
    assert len(data["points"]) == 14
    assert "total" in data
    assert "delta_pct" in data


async def test_timeseries_new_users_admin_ok(client, make_user):
    admin = await make_user(role="admin")
    r = await client.get(
        "/api/admin/stats/timeseries",
        params={"telegram_id": admin.telegram_id, "metric": "new_users", "days": 7},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["metric"] == "new_users"
    assert data["days"] == 7
    assert len(data["points"]) == 7
    # the admin user we just made must count toward today's bucket
    assert data["total"] >= 1


async def test_timeseries_bad_metric_422(client, make_user):
    admin = await make_user(role="admin")
    r = await client.get(
        "/api/admin/stats/timeseries",
        params={"telegram_id": admin.telegram_id, "metric": "not_a_metric", "days": 30},
    )
    assert r.status_code == 422


async def test_timeseries_days_out_of_range_422(client, make_user):
    admin = await make_user(role="admin")
    r0 = await client.get(
        "/api/admin/stats/timeseries",
        params={"telegram_id": admin.telegram_id, "metric": "karma_earned", "days": 0},
    )
    assert r0.status_code == 422

    r200 = await client.get(
        "/api/admin/stats/timeseries",
        params={"telegram_id": admin.telegram_id, "metric": "karma_earned", "days": 200},
    )
    assert r200.status_code == 422


async def test_timeseries_unauthenticated_401(client):
    r = await client.get(
        "/api/admin/stats/timeseries",
        params={"metric": "karma_earned", "days": 30},
    )
    assert r.status_code == 401


async def test_timeseries_fills_missing_days(client, make_user, db_session):
    """One earned tx today + 7-day window → 7 points, only today is non-zero."""
    admin = await make_user(role="admin")
    user = await make_user(credits=Decimal("10"), total_credits_earned=Decimal("10"))
    db_session.add(Transaction(
        user_id=user.id,
        type=TransactionType.EARNED,
        amount=Decimal("12.5"),
        balance_after=Decimal("12.5"),
        idempotency_key=uuid.uuid4().hex,
        description="test earn",
    ))
    await db_session.commit()

    r = await client.get(
        "/api/admin/stats/timeseries",
        params={"telegram_id": admin.telegram_id, "metric": "karma_earned", "days": 7},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert len(data["points"]) == 7
    # exactly one bucket has a non-zero value, and it equals our 12.5
    non_zero = [p for p in data["points"] if p["value"] != 0]
    assert len(non_zero) == 1
    assert non_zero[0]["value"] == 12.5
    # so the other 6 days are zero-filled
    zero_points = [p for p in data["points"] if p["value"] == 0]
    assert len(zero_points) == 6
    # total reflects just the one row
    assert data["total"] == 12.5
