"""GET /user/waitlist-enrichment/ — the caller's STORED score for the card.

Scores are fetched only at sign-up and on the Refresh button, so this read
must never call the provider: every test installs a provider that explodes.
"""
from datetime import datetime

import pytest

from app.api import users as users_api
from app.models.waitlist_entry import WaitlistEntry
from app.repositories.x_profile import XProfileRepository
from app.services import scores as scores_svc


class _ExplodingProvider:
    async def get_user_data(self, username):
        raise AssertionError("enrichment must not call the score provider")

    async def get_top_followers(self, username, k=10):
        raise AssertionError("enrichment must not call the score provider")


@pytest.fixture(autouse=True)
def _no_provider_calls(monkeypatch):
    monkeypatch.setattr(scores_svc, "get_score_client", lambda: _ExplodingProvider())
    # the endpoint module must not reach the provider on its own either
    assert not hasattr(users_api, "get_score_client")


FETCHED = datetime(2026, 9, 17, 9, 0)
PAYLOAD = {
    "score": 587.2, "screen_name": "0xBlest_", "smart_followers": 618,
    "top_followers": [{"username": f"acct{i}"} for i in range(12)],
}


async def test_waitlisted_user_reads_the_entry(client, db_session):
    """PRIMARY use case — on the waitlist, no User row yet."""
    db_session.add(WaitlistEntry(
        telegram_id=7700, x_username="waitlisted_dev", referral_code="WL0000ABCD",
        score=587.2, score_data=PAYLOAD, score_updated_at=FETCHED,
    ))
    await db_session.commit()

    r = await client.get("/user/waitlist-enrichment/", params={"telegram_id": 7700})
    assert r.status_code == 200
    assert r.json() == {
        "x_username": "waitlisted_dev",
        "score": 587.2,
        "tier": "Based",  # 400 <= 587 < 600
        "followers": [f"acct{i}" for i in range(10)],  # capped at 10
        "followers_count": 618,                        # the provider's total
        "score_status": "ready",
        "score_updated_at": "2026-09-17T09:00:00",
    }


async def test_pending_until_the_signup_fetch_lands(client, db_session):
    db_session.add(WaitlistEntry(telegram_id=7701, x_username="brand_new", referral_code="WL0000ABCE"))
    await db_session.commit()
    body = (await client.get("/user/waitlist-enrichment/", params={"telegram_id": 7701})).json()
    assert body["score"] is None
    assert body["score_status"] == "pending"
    assert body["followers"] == [] and body["followers_count"] == 0


async def test_not_found_after_a_fetch_without_score(client, db_session):
    db_session.add(WaitlistEntry(
        telegram_id=7702, x_username="small_acct", referral_code="WL0000ABCF",
        score_updated_at=FETCHED,
    ))
    await db_session.commit()
    body = (await client.get("/user/waitlist-enrichment/", params={"telegram_id": 7702})).json()
    assert body["score"] is None
    assert body["score_status"] == "not_found"


async def test_approved_user_reads_user_and_profile(client, make_user, db_session):
    user = await make_user(
        telegram_id=5100, x_username="0xBlest_", tweetscout_score=450,
        tweetscout_last_updated=FETCHED,
    )
    await XProfileRepository(db_session).create(
        user_id=user.id, username="0xBlest_", raw_tweetscout_data={
            "score": 450, "top_followers": [{"username": "elonmusk"}, {"username": "vitalikbuterin"}],
        },
    )
    await db_session.commit()

    body = (await client.get("/user/waitlist-enrichment/", params={"telegram_id": 5100})).json()
    assert body["x_username"] == "0xBlest_"
    assert body["score"] == 450.0
    assert body["tier"] == "Based"
    assert body["followers"] == ["elonmusk", "vitalikbuterin"]
    assert body["followers_count"] == 2  # no provider total stored -> what we show
    assert body["score_status"] == "ready"


async def test_approved_user_never_scored_is_pending(client, make_user):
    await make_user(telegram_id=5101, x_username="ghost")
    body = (await client.get("/user/waitlist-enrichment/", params={"telegram_id": 5101})).json()
    assert body["x_username"] == "ghost"
    assert body["score"] is None and body["tier"] is None
    assert body["score_status"] == "pending"


async def test_no_x_username(client, make_user):
    await make_user(telegram_id=5102, x_username=None)
    body = (await client.get("/user/waitlist-enrichment/", params={"telegram_id": 5102})).json()
    assert body["x_username"] is None
    assert body["score"] is None and body["followers"] == []


async def test_stranger_returns_empty(client):
    """Verified telegram_id with no User AND no WaitlistEntry — empty shape, 200."""
    r = await client.get("/user/waitlist-enrichment/", params={"telegram_id": 888888})
    assert r.status_code == 200
    assert r.json() == {
        "x_username": None, "score": None, "tier": None,
        "followers": [], "followers_count": 0,
        "score_status": "not_found", "score_updated_at": None,
    }


async def test_requires_identity(client):
    """No telegram_id + no init-data header -> 401 from get_telegram_identity."""
    r = await client.get("/user/waitlist-enrichment/")
    assert r.status_code == 401
