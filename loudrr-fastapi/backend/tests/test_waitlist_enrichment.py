import pytest

from app.api import users as users_api
from app.models.waitlist_entry import WaitlistEntry


class _FakeAnalytics:
    def __init__(self, profile=None, top=None):
        self._profile = profile
        self._top = top

    async def get_user_data(self, username):
        return self._profile

    async def get_top_followers(self, username, k=10):
        return self._top


@pytest.mark.asyncio
async def test_waitlist_enrichment_up(client, make_user, monkeypatch):
    """Approved user (User row exists) — enrichment reads x_username off User."""
    await make_user(telegram_id=5100, x_username="0xBlest_")
    profile = {"score": 450, "screen_name": "0xBlest_", "followers_count": 12}
    top = [{"username": "elonmusk"}, {"username": "vitalikbuterin"}]
    monkeypatch.setattr(
        users_api, "get_score_client",
        lambda: _FakeAnalytics(profile=profile, top=top),
    )
    r = await client.get("/user/waitlist-enrichment/", params={"telegram_id": 5100})
    assert r.status_code == 200
    body = r.json()
    assert body["x_username"] == "0xBlest_"
    assert body["score"] == 450.0
    assert body["tier"] == "Based"  # 400 <= 450 < 600
    assert body["followers"] == ["elonmusk", "vitalikbuterin"]
    assert body["followers_count"] == 2


@pytest.mark.asyncio
async def test_waitlist_enrichment_waitlisted_user(client, db_session, monkeypatch):
    """PRIMARY use case — user is on the waitlist (WaitlistEntry present, NO
    User row yet). The endpoint MUST resolve x_username from WaitlistEntry
    and return enriched data. This is the scenario the endpoint was built for."""
    entry = WaitlistEntry(
        telegram_id=7700,
        x_username="waitlisted_dev",
        referral_code="WL0000ABCD",
    )
    db_session.add(entry)
    await db_session.commit()

    profile = {"score": 250, "screen_name": "waitlisted_dev"}
    top = [{"username": "vitalikbuterin"}, {"username": "sama"}]
    monkeypatch.setattr(
        users_api, "get_score_client",
        lambda: _FakeAnalytics(profile=profile, top=top),
    )
    r = await client.get("/user/waitlist-enrichment/", params={"telegram_id": 7700})
    assert r.status_code == 200
    body = r.json()
    assert body["x_username"] == "waitlisted_dev"
    assert body["score"] == 250.0
    assert body["tier"] == "Degen"  # 200 <= 250 < 400
    assert body["followers"] == ["vitalikbuterin", "sama"]


@pytest.mark.asyncio
async def test_waitlist_enrichment_analytics_down(client, make_user, monkeypatch):
    """Analytics returns None for both calls — endpoint returns the empty
    shape as 200 (never 500)."""
    await make_user(telegram_id=5101, x_username="ghost")
    monkeypatch.setattr(
        users_api, "get_score_client",
        lambda: _FakeAnalytics(profile=None, top=None),
    )
    r = await client.get("/user/waitlist-enrichment/", params={"telegram_id": 5101})
    assert r.status_code == 200
    body = r.json()
    assert body["score"] is None
    assert body["tier"] is None
    assert body["followers"] == []
    assert body["followers_count"] == 0
    assert body["x_username"] == "ghost"


@pytest.mark.asyncio
async def test_waitlist_enrichment_no_x_username(client, make_user, monkeypatch):
    """User exists but hasn't linked X — empty payload, and analytics is
    never even called (we short-circuit on missing handle)."""
    await make_user(telegram_id=5102, x_username=None)
    # Even if analytics would return data, no handle -> empty. Patch to prove it.
    monkeypatch.setattr(
        users_api, "get_score_client",
        lambda: _FakeAnalytics(profile={"score": 999}, top=[{"username": "x"}]),
    )
    r = await client.get("/user/waitlist-enrichment/", params={"telegram_id": 5102})
    assert r.status_code == 200
    body = r.json()
    assert body["x_username"] is None
    assert body["score"] is None
    assert body["tier"] is None
    assert body["followers"] == []
    assert body["followers_count"] == 0


@pytest.mark.asyncio
async def test_waitlist_enrichment_stranger_returns_empty(client, monkeypatch):
    """Verified telegram_id with no User AND no WaitlistEntry — returns empty
    shape as 200 (not 401). The identity dep authenticates; the endpoint just
    has nothing to enrich with."""
    monkeypatch.setattr(
        users_api, "get_score_client",
        lambda: _FakeAnalytics(profile={"score": 500}, top=[{"username": "x"}]),
    )
    r = await client.get("/user/waitlist-enrichment/", params={"telegram_id": 888888})
    assert r.status_code == 200
    body = r.json()
    assert body == {
        "x_username": None, "score": None, "tier": None,
        "followers": [], "followers_count": 0,
    }


@pytest.mark.asyncio
async def test_waitlist_enrichment_requires_identity(client):
    """No telegram_id + no init-data header -> 401 from get_telegram_identity."""
    r = await client.get("/user/waitlist-enrichment/")
    assert r.status_code == 401
