"""Integration tests for the Ch10 user endpoints (2, 3, 4, 8).

TweetScout is mocked — these tests never hit the network. The debug-bypass
(`?telegram_id=`) authenticates, since settings.debug is True in tests.
"""

from app.repositories.x_profile import XProfileRepository
from app.services import users as svc


class _FakeTweetScout:
    """Stand-in for TweetScoutClient: returns a fixed flat payload (or None)."""

    def __init__(self, data):
        self._data = data

    async def get_user_data(self, username):
        return self._data


def _mock_tweetscout(monkeypatch, data):
    # svc imports `get_score_client` (renamed from get_tweetscout_client when
    # the TweetScout call moved behind the loudrr_analytics abstraction).
    monkeypatch.setattr(svc, "get_score_client", lambda: _FakeTweetScout(data))


_PROFILE = {
    "id": "1456366493323644928",
    "name": "Blest",
    "screen_name": "0xBlest_",
    "description": "gm",
    "followers_count": 10186,
    "friends_count": 9754,
    "tweets_count": 60051,
    "avatar": "https://x/avatar.png",
    "banner": "https://x/banner.png",
    "verified": True,
    "can_dm": True,
    "register_date": "2021-11-04",
    "score": 450,
}


# ---- GET /user/ ----
async def test_user_info_shape(client, make_user):
    await make_user(telegram_id=5001, telegram_username="zoe", display_name="Zoe")
    r = await client.get("/user/", params={"telegram_id": 5001})
    assert r.status_code == 200
    body = r.json()
    assert body["telegram_username"] == "zoe"
    assert body["tier"] == "Anon"           # score 0
    assert body["daily_cap"] == 100          # seeded by conftest
    assert body["credits"] == 0.0
    assert body["x_username"] is None
    assert body["available_posts"] == 0      # stubbed until Ch12
    assert body["x_verification_pending_review"] is False
    assert body["honesty_score"] == 50
    # streak fields — fresh user has no streak data; 7 is the next milestone
    assert body["current_streak"] == 0
    assert body["longest_streak"] == 0
    assert body["streak_multiplier"] == 1.0
    assert body["streak_next_milestone"] == 7


async def test_user_info_unknown_user_401(client):
    r = await client.get("/user/", params={"telegram_id": 999999})
    assert r.status_code == 401


# ---- GET /user/stats/ ----
async def test_user_stats_shape(client, make_user):
    await make_user(telegram_id=5002, telegram_username="ana")
    r = await client.get("/user/stats/", params={"telegram_id": 5002})
    assert r.status_code == 200
    body = r.json()
    assert body["user"]["telegram_username"] == "ana"
    assert body["posts"] == {"total": 0, "active": 0, "completed": 0}
    assert body["engagements"] == {"given": 0, "received": 0}
    assert body["recent_posts"] == []
    # streak fields surfaced on /user/stats/ for the front-end progress card
    assert body["user"]["current_streak"] == 0
    assert body["user"]["longest_streak"] == 0
    assert body["user"]["streak_multiplier"] == 1.0
    assert body["user"]["streak_next_milestone"] == 7


# ---- POST /user/link-x/ ----
async def test_link_x_success(client, make_user, db_session, monkeypatch):
    user = await make_user(telegram_id=5003)
    _mock_tweetscout(monkeypatch, _PROFILE)

    r = await client.post(
        "/user/link-x/", params={"telegram_id": 5003}, json={"x_username": "@0xBlest_"}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["tweetscout_score"] == 450
    assert body["tier"] == "Based"           # 400 ≤ 450 < 600
    assert body["followers_count"] == 10186
    assert body["x_username"] == "0xBlest_"

    # the profile was cached
    profile = await XProfileRepository(db_session).get(user_id=user.id)
    assert profile is not None
    assert profile.score == 450
    assert profile.followers_count == 10186


async def test_link_x_invalid_username_400(client, make_user, monkeypatch):
    await make_user(telegram_id=5004)
    _mock_tweetscout(monkeypatch, _PROFILE)  # shouldn't be reached
    r = await client.post(
        "/user/link-x/", params={"telegram_id": 5004}, json={"x_username": "not valid!"}
    )
    assert r.status_code == 400
    assert "error" in r.json()


async def test_link_x_not_found_400(client, make_user, monkeypatch):
    await make_user(telegram_id=5005)
    _mock_tweetscout(monkeypatch, None)  # TweetScout has no such user
    r = await client.post(
        "/user/link-x/", params={"telegram_id": 5005}, json={"x_username": "ghost"}
    )
    assert r.status_code == 400
    assert "error" in r.json()


# ---- POST /onboarding/complete/ ----
async def test_onboarding_requires_x_username(client, make_user):
    await make_user(telegram_id=5006)  # no x_username
    r = await client.post("/onboarding/complete/", params={"telegram_id": 5006})
    assert r.status_code == 400


async def test_onboarding_success(client, make_user, monkeypatch):
    await make_user(telegram_id=5007, x_username="0xBlest_")
    _mock_tweetscout(monkeypatch, _PROFILE)
    r = await client.post("/onboarding/complete/", params={"telegram_id": 5007})
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["tweetscout_score"] == 450
    assert body["tier"] == "Based"


async def test_onboarding_already_onboarded(client, make_user, monkeypatch):
    await make_user(telegram_id=5008, x_username="0xBlest_", tweetscout_score=450)
    _mock_tweetscout(monkeypatch, None)  # must NOT be needed
    r = await client.post("/onboarding/complete/", params={"telegram_id": 5008})
    assert r.status_code == 200
    body = r.json()
    assert body["already_onboarded"] is True
    assert body["tier"] == "Based"


async def test_onboarding_api_down_benefit_of_doubt(client, make_user, monkeypatch):
    await make_user(telegram_id=5009, x_username="0xBlest_")
    _mock_tweetscout(monkeypatch, None)  # TweetScout unavailable
    r = await client.post("/onboarding/complete/", params={"telegram_id": 5009})
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["tweetscout_score"] == 0
    assert body["tier"] == "Anon"
    assert "message" in body
