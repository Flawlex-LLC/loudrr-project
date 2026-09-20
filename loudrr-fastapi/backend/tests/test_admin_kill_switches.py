"""Kill switches — the five boolean settings that stop a write path dead.

One test per switch proving the gated path actually refuses (with a message a
user can read), one proving it still works when the switch is on, plus the
MAINTENANCE_MODE master and the fact that the switches are reachable through
the admin settings API like every other setting.

The switches are read fresh on every check (they bypass the 300s settings
cache), so these tests flip a SiteSetting row directly and expect the very
next call to see it.
"""
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from app.core.errors import ServiceUnavailable
from app.models.site_setting import SiteSetting
from app.services import kill_switches
from app.services import posts as posts_svc
from app.services import sponsors as sponsors_svc


async def _set_switch(db, key: str, value: bool) -> None:
    from sqlalchemy import select

    row = (
        await db.execute(select(SiteSetting).where(SiteSetting.key == key))
    ).scalar_one_or_none()
    if row is None:
        db.add(SiteSetting(key=key, value="true" if value else "false", data_type="bool"))
    else:
        row.value = "true" if value else "false"
    await db.commit()


# ---------------------------------------------------------------------------
# The helper itself
# ---------------------------------------------------------------------------
async def test_defaults_are_open_when_unseeded(db_session):
    """A missing row must never take the product down by accident."""
    for key in (kill_switches.POSTS_ENABLED, kill_switches.CLAIMS_ENABLED,
                kill_switches.WAITLIST_REGISTRATION_ENABLED,
                kill_switches.SPONSOR_INGEST_ENABLED):
        assert await kill_switches.is_enabled(db_session, key) is True
    assert await kill_switches.is_enabled(db_session, kill_switches.MAINTENANCE_MODE) is False


async def test_maintenance_mode_closes_everything(db_session):
    await _set_switch(db_session, kill_switches.MAINTENANCE_MODE, True)
    for key in (kill_switches.POSTS_ENABLED, kill_switches.CLAIMS_ENABLED,
                kill_switches.WAITLIST_REGISTRATION_ENABLED,
                kill_switches.SPONSOR_INGEST_ENABLED):
        assert await kill_switches.is_enabled(db_session, key) is False
        with pytest.raises(ServiceUnavailable) as exc:
            await kill_switches.require_enabled(db_session, key, "path message")
        # the master's message wins over the individual path's
        assert "maintenance" in str(exc.value).lower()


async def test_switch_change_is_seen_immediately(db_session):
    """No 300s cache wait: flip the row, the very next read sees it."""
    assert await kill_switches.is_enabled(db_session, kill_switches.POSTS_ENABLED) is True
    await _set_switch(db_session, kill_switches.POSTS_ENABLED, False)
    assert await kill_switches.is_enabled(db_session, kill_switches.POSTS_ENABLED) is False
    await _set_switch(db_session, kill_switches.POSTS_ENABLED, True)
    assert await kill_switches.is_enabled(db_session, kill_switches.POSTS_ENABLED) is True


# ---------------------------------------------------------------------------
# POSTS_ENABLED — services/posts.py submit_post
# ---------------------------------------------------------------------------
async def test_posts_switch_refuses_submission(db_session, make_user):
    user = await make_user(x_username="poster", credits=Decimal("500"),
                           total_credits_earned=Decimal("500"))
    await _set_switch(db_session, kill_switches.POSTS_ENABLED, False)
    with pytest.raises(ServiceUnavailable) as exc:
        await posts_svc.submit_post(
            db_session, user=user, x_link="https://x.com/a/status/1", karma_amount=80,
        )
    assert "paused" in str(exc.value).lower()


async def test_posts_switch_off_refuses_before_any_twitter_call(db_session, make_user):
    """The gate sits above the external fetch — no provider spend while off."""
    user = await make_user(x_username="poster", credits=Decimal("500"),
                           total_credits_earned=Decimal("500"))
    await _set_switch(db_session, kill_switches.POSTS_ENABLED, False)
    with patch("app.services.posts.get_twitter_client") as client_factory:
        with pytest.raises(ServiceUnavailable):
            await posts_svc.submit_post(
                db_session, user=user, x_link="https://x.com/a/status/1",
            )
        client_factory.assert_not_called()


async def test_posts_switch_on_gets_past_the_gate(db_session, make_user):
    """With the switch on, submit_post fails on its NORMAL validation instead
    (proves the gate isn't blocking the happy path)."""
    from app.core.errors import BadRequest

    user = await make_user(x_username="", credits=Decimal("500"),
                           total_credits_earned=Decimal("500"))
    await _set_switch(db_session, kill_switches.POSTS_ENABLED, True)
    with pytest.raises(BadRequest) as exc:
        await posts_svc.submit_post(
            db_session, user=user, x_link="https://x.com/a/status/1",
        )
    assert "X account" in str(exc.value)


# ---------------------------------------------------------------------------
# CLAIMS_ENABLED — services/claims.py queue_claim
# ---------------------------------------------------------------------------
async def test_claims_switch_refuses_new_claim(db_session, make_user):
    from app.services import claims as claims_svc

    user = await make_user(x_username="claimer")
    await _set_switch(db_session, kill_switches.CLAIMS_ENABLED, False)
    body, status = await claims_svc.queue_claim(
        db_session, user=user, schedule=AsyncMock(),
    )
    assert status == 503
    assert body["success"] is False
    assert body["error"] == "claims_disabled"
    assert "paused" in body["message"].lower()
    assert "pending_count" in body


async def test_claims_switch_on_reaches_the_normal_gates(db_session, make_user):
    from app.services import claims as claims_svc

    user = await make_user(x_username="")  # no X account → the normal 400
    await _set_switch(db_session, kill_switches.CLAIMS_ENABLED, True)
    body, status = await claims_svc.queue_claim(
        db_session, user=user, schedule=AsyncMock(),
    )
    assert status == 400
    assert body["error"] == "x_account_required"


# ---------------------------------------------------------------------------
# WAITLIST_REGISTRATION_ENABLED — api/waitlist.py register
# ---------------------------------------------------------------------------
async def test_waitlist_switch_refuses_registration(client, db_session, confirmed_x_proof):
    tg_id = 951001
    proof = await confirmed_x_proof(tg_id, "newbie", "77")
    await _set_switch(db_session, kill_switches.WAITLIST_REGISTRATION_ENABLED, False)
    r = await client.post(
        "/waitlist/register/",
        params={"telegram_id": tg_id},
        json={"x_proof": proof, "region": "europe", "niche": "defi"},
    )
    assert r.status_code == 503, r.text
    assert "closed" in r.text.lower()

    # and nothing was written
    from sqlalchemy import func, select

    from app.models.waitlist_entry import WaitlistEntry
    count = (await db_session.execute(select(func.count(WaitlistEntry.id)))).scalar_one()
    assert count == 0


async def test_waitlist_switch_on_allows_registration(client, db_session, confirmed_x_proof):
    tg_id = 951002
    proof = await confirmed_x_proof(tg_id, "newbie2", "78")
    await _set_switch(db_session, kill_switches.WAITLIST_REGISTRATION_ENABLED, True)
    r = await client.post(
        "/waitlist/register/",
        params={"telegram_id": tg_id},
        json={"x_proof": proof, "region": "europe", "niche": "defi"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "registered"


# ---------------------------------------------------------------------------
# SPONSOR_INGEST_ENABLED — services/sponsors.py ingest_tweet
# ---------------------------------------------------------------------------
async def _active_sponsor(db_session):
    from app.models.sponsored_account import SponsoredAccount
    from app.core.time_utils import utcnow
    from datetime import timedelta

    sponsor = SponsoredAccount(
        x_username="sponsorco", x_user_id="42", display_name="Sponsor Co",
        is_active=True, karma_per_post=Decimal("100"),
        active_since=utcnow() - timedelta(hours=1),
    )
    db_session.add(sponsor)
    await db_session.commit()
    return sponsor


def _tweet(tweet_id="1234567890"):
    from app.core.time_utils import utcnow

    return {
        "id": tweet_id,
        "text": "gm",
        "createdAt": utcnow().isoformat(),
        "author": {"id": "42", "userName": "sponsorco", "name": "Sponsor Co"},
        "isReply": False,
        "retweeted_tweet": None,
    }


async def test_sponsor_ingest_switch_skips_tweets(db_session):
    await _active_sponsor(db_session)
    await _set_switch(db_session, kill_switches.SPONSOR_INGEST_ENABLED, False)
    assert await sponsors_svc.ingest_tweet(db_session, _tweet()) is None


async def test_sponsor_ingest_switch_on_creates_post(db_session):
    await _active_sponsor(db_session)
    await _set_switch(db_session, kill_switches.SPONSOR_INGEST_ENABLED, True)
    post = await sponsors_svc.ingest_tweet(db_session, _tweet("1234567891"))
    assert post is not None
    assert post.is_sponsored is True


# ---------------------------------------------------------------------------
# The admin settings API exposes them as ordinary (bool, live, danger) settings
# ---------------------------------------------------------------------------
async def test_kill_switches_surface_in_settings_list(client, make_user):
    admin = await make_user(role="admin")
    r = await client.get(
        "/api/admin/site-settings/", params={"telegram_id": admin.telegram_id}
    )
    assert r.status_code == 200
    data = r.json()
    group = next(g for g in data["groups"] if g["name"] == "Kill switches")
    by_key = {s["key"]: s for s in group["settings"]}
    assert set(by_key) == set(kill_switches.ALL_KEYS)
    for spec in by_key.values():
        assert spec["data_type"] == "bool"
        assert spec["live"] is True
        assert spec["danger"] is True
        assert spec["impact"]
    # kill switches are pinned first so they can't be buried under 60 tunables
    assert data["groups"][0]["name"] == "Kill switches"


async def test_kill_switch_toggles_through_the_api(client, make_user, db_session):
    admin = await make_user(role="superadmin")
    r = await client.put(
        "/api/admin/site-settings/POSTS_ENABLED/",
        params={"telegram_id": admin.telegram_id},
        json={"value": "false"},
    )
    assert r.status_code == 200, r.text
    assert await kill_switches.is_enabled(db_session, kill_switches.POSTS_ENABLED) is False


async def test_kill_switch_rejects_non_bool(client, make_user):
    admin = await make_user(role="superadmin")
    r = await client.put(
        "/api/admin/site-settings/MAINTENANCE_MODE/",
        params={"telegram_id": admin.telegram_id},
        json={"value": "maybe"},
    )
    assert r.status_code == 422
