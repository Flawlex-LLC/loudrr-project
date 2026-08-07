"""Ch14 — post submission (endpoint 14) + escrow cancel. Twitter is mocked."""
from decimal import Decimal


from app.models.post import Post
from app.models.site_setting import SiteSetting
from app.models.x_profile import XProfile
from app.repositories.post import PostRepository
from app.services import posts as posts_svc
from app.services import site_settings


class _FakeTwitter:
    def __init__(self, content):
        self._c = content

    async def get_tweet_content(self, tweet_id):
        return self._c


def _mock_twitter(monkeypatch, content):
    monkeypatch.setattr(posts_svc, "get_twitter_client", lambda: _FakeTwitter(content))


_CONTENT = {
    "tweet_id": "123", "text": "gm world", "author_id": "X1",
    "author_username": "me", "author_name": "Me", "author_avatar": "",
    "media": [], "created_at": "",
}

LINK = "https://x.com/me/status/123"


async def _seed_costs(db, lo=10, hi=200):
    db.add(SiteSetting(key="POST_COST_MIN", value=str(lo), data_type="int"))
    db.add(SiteSetting(key="POST_COST_MAX", value=str(hi), data_type="int"))
    await db.commit()
    site_settings._cache.clear()


async def _linked_user(make_user, db, telegram_id, *, credits="100"):
    user = await make_user(
        telegram_id=telegram_id, x_username="me",
        credits=Decimal(credits), total_credits_earned=Decimal(credits),
    )
    db.add(XProfile(user_id=user.id, x_user_id="X1", username="me", score=0))
    await db.commit()
    return user


async def test_submit_happy(client, make_user, db_session, monkeypatch):
    await _seed_costs(db_session)
    user = await _linked_user(make_user, db_session, 9001, credits="100")
    _mock_twitter(monkeypatch, _CONTENT)

    r = await client.post(
        "/post/submit/", params={"telegram_id": 9001},
        json={"x_link": LINK, "karma_amount": 50},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["escrow"] == 50
    assert body["new_balance"] == 50.0  # 100 − 50 escrowed

    post = await PostRepository(db_session).get(id=body["post_id"])
    assert post is not None
    assert post.escrow == Decimal("50.0000")
    assert post.status == "active"
    assert post.tweet_text == "gm world"
    await db_session.refresh(user)
    assert user.total_posts == 1


async def test_submit_defaults_to_min(client, make_user, db_session, monkeypatch):
    await _seed_costs(db_session, lo=10, hi=200)
    await _linked_user(make_user, db_session, 9002)
    _mock_twitter(monkeypatch, _CONTENT)
    r = await client.post("/post/submit/", params={"telegram_id": 9002}, json={"x_link": LINK})
    assert r.json()["escrow"] == 10  # POST_COST_MIN


async def test_submit_ownership_mismatch_400(client, make_user, db_session, monkeypatch):
    await _seed_costs(db_session)
    await _linked_user(make_user, db_session, 9003)
    _mock_twitter(monkeypatch, {**_CONTENT, "author_id": "SOMEONE_ELSE"})
    r = await client.post("/post/submit/", params={"telegram_id": 9003}, json={"x_link": LINK})
    assert r.status_code == 400
    assert "own posts" in r.json()["error"]


async def test_submit_insufficient_karma_400(client, make_user, db_session, monkeypatch):
    await _seed_costs(db_session)
    await _linked_user(make_user, db_session, 9004, credits="5")  # < min 10
    _mock_twitter(monkeypatch, _CONTENT)
    r = await client.post(
        "/post/submit/", params={"telegram_id": 9004}, json={"x_link": LINK, "karma_amount": 50}
    )
    assert r.status_code == 400
    assert "Not enough karma" in r.json()["error"]


async def test_submit_no_x_username_400(client, make_user, db_session, monkeypatch):
    await _seed_costs(db_session)
    await make_user(telegram_id=9005)  # no x_username, no XProfile
    _mock_twitter(monkeypatch, _CONTENT)
    r = await client.post("/post/submit/", params={"telegram_id": 9005}, json={"x_link": LINK})
    assert r.status_code == 400


async def test_submit_invalid_link_400(client, make_user, db_session, monkeypatch):
    await _seed_costs(db_session)
    await _linked_user(make_user, db_session, 9006)
    _mock_twitter(monkeypatch, _CONTENT)
    r = await client.post(
        "/post/submit/", params={"telegram_id": 9006}, json={"x_link": "https://example.com/foo"}
    )
    assert r.status_code == 400


async def test_submit_tweet_fetch_fails_503(client, make_user, db_session, monkeypatch):
    await _seed_costs(db_session)
    await _linked_user(make_user, db_session, 9007)
    _mock_twitter(monkeypatch, None)  # API down / not found
    r = await client.post("/post/submit/", params={"telegram_id": 9007}, json={"x_link": LINK})
    assert r.status_code == 503


async def test_submit_over_max_400(client, make_user, db_session, monkeypatch):
    await _seed_costs(db_session, lo=10, hi=100)
    await _linked_user(make_user, db_session, 9008, credits="1000")
    _mock_twitter(monkeypatch, _CONTENT)
    r = await client.post(
        "/post/submit/", params={"telegram_id": 9008}, json={"x_link": LINK, "karma_amount": 500}
    )
    assert r.status_code == 400
    assert "Maximum karma" in r.json()["error"]


async def test_submit_at_exact_min_and_max_boundaries(client, make_user, db_session, monkeypatch):
    await _seed_costs(db_session, lo=10, hi=100)
    await _linked_user(make_user, db_session, 9010, credits="1000")
    _mock_twitter(monkeypatch, _CONTENT)
    # exactly the minimum is allowed
    r_min = await client.post(
        "/post/submit/", params={"telegram_id": 9010},
        json={"x_link": "https://x.com/me/status/501", "karma_amount": 10},
    )
    assert r_min.status_code == 200 and r_min.json()["escrow"] == 10
    # exactly the maximum is allowed
    r_max = await client.post(
        "/post/submit/", params={"telegram_id": 9010},
        json={"x_link": "https://x.com/me/status/502", "karma_amount": 100},
    )
    assert r_max.status_code == 200 and r_max.json()["escrow"] == 100


async def test_submit_one_below_min_400(client, make_user, db_session, monkeypatch):
    await _seed_costs(db_session, lo=10, hi=100)
    await _linked_user(make_user, db_session, 9011, credits="1000")
    _mock_twitter(monkeypatch, _CONTENT)
    r = await client.post(
        "/post/submit/", params={"telegram_id": 9011},
        json={"x_link": LINK, "karma_amount": 9},  # one under the floor
    )
    assert r.status_code == 400
    assert "Minimum karma" in r.json()["error"]


async def test_submit_duplicate_active_post_400(client, make_user, db_session, monkeypatch):
    await _seed_costs(db_session)
    await _linked_user(make_user, db_session, 9012, credits="1000")
    _mock_twitter(monkeypatch, _CONTENT)
    first = await client.post(
        "/post/submit/", params={"telegram_id": 9012}, json={"x_link": LINK, "karma_amount": 10}
    )
    assert first.status_code == 200
    # the same link, still active → refused (no double escrow on one post)
    dup = await client.post(
        "/post/submit/", params={"telegram_id": 9012}, json={"x_link": LINK, "karma_amount": 10}
    )
    assert dup.status_code == 400
    assert "already active" in dup.json()["error"]


async def test_cancel_refunds_escrow(make_user, db_session):
    owner = await make_user(telegram_id=9009, credits=Decimal("0"), total_credits_earned=Decimal("0"))
    post = Post(
        user_id=owner.id, x_link=LINK, tweet_id="123",
        escrow=Decimal("50"), initial_escrow=Decimal("50"), status="active", platform="web",
    )
    db_session.add(post)
    await db_session.commit()

    await posts_svc.cancel_post(db_session, post, refund=True)
    assert post.status == "cancelled"
    assert post.escrow == Decimal("0.0000")
    await db_session.refresh(owner)
    assert owner.credits == Decimal("50.0000")  # escrow refunded
