"""Guards around the platform user that owns sponsored posts.

A sponsored post belongs to sponsors.PLATFORM_USER_ID, not a real poster, so
the usual `post.user_id == user.id` checks miss two things: the sponsor's own
Loudrr account (matched by X handle instead) must not raid its own sponsored
posts, and the platform user must not show up as a person in the admin
console or be granted/revoked/banned there.
"""
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.core.errors import BadRequest
from app.core.time_utils import utcnow
from app.models.engagement import Engagement
from app.models.post import Post
from app.models.sponsored_account import SponsoredAccount
from app.models.user import User
from app.services import admin as admin_svc
from app.services import feed, sponsors
from app.services.sponsors import PLATFORM_USER_ID


# ---------------------------------------------------------------------------
# helpers (small copies of the ones in test_sponsors.py)
# ---------------------------------------------------------------------------
def x_time(dt: datetime) -> str:
    """X's createdAt format for a naive-UTC datetime."""
    return dt.strftime("%a %b %d %H:%M:%S +0000 %Y")


def make_tweet(tweet_id, *, user="Acme", author_id="111"):
    return {
        "id": str(tweet_id), "text": "gm raiders",
        "createdAt": x_time(utcnow() - timedelta(minutes=1)),
        "author": {"id": author_id, "userName": user, "name": "Acme Inc",
                   "profilePicture": "https://pbs.twimg.com/acme.jpg"},
    }


async def sponsored_post(db, *, handle="acme", tweet_user="Acme") -> Post:
    """An active sponsor @handle and one sponsored post of its (author @tweet_user)."""
    db.add(SponsoredAccount(
        x_username=handle, x_user_id="111", display_name="Acme", avatar_url="",
        karma_per_post=Decimal("100"), active_since=utcnow() - timedelta(hours=1),
    ))
    await db.commit()
    post = await sponsors.ingest_tweet(db, make_tweet("100", user=tweet_user))
    await db.commit()
    assert post is not None and post.platform == "sponsor"
    assert post.user_id == PLATFORM_USER_ID
    return post


async def _feed_ids(db, user) -> list:
    return [p.id for p in await feed.get_feed_posts(db, user)]


# ---------------------------------------------------------------------------
# 1. the sponsor's own account never gets its sponsored posts
# ---------------------------------------------------------------------------
async def test_feed_hides_sponsored_post_from_the_sponsor_account(db_session, make_user):
    post = await sponsored_post(db_session)
    sponsor_user = await make_user(x_username="acme")
    other = await make_user(x_username="someone_else")

    assert await _feed_ids(db_session, sponsor_user) == []
    assert await feed.get_feed_count(db_session, sponsor_user) == 0

    assert await _feed_ids(db_session, other) == [post.id]
    assert await feed.get_feed_count(db_session, other) == 1


@pytest.mark.parametrize("x_username", ["ACME", "Acme", "aCmE"])
async def test_feed_sponsor_match_is_case_insensitive(db_session, make_user, x_username):
    await sponsored_post(db_session, tweet_user="acme")
    sponsor_user = await make_user(x_username=x_username)

    assert await _feed_ids(db_session, sponsor_user) == []
    assert await feed.get_feed_count(db_session, sponsor_user) == 0


@pytest.mark.parametrize("x_username", [None, ""])
async def test_feed_user_without_x_username_still_sees_sponsored_post(db_session, make_user, x_username):
    post = await sponsored_post(db_session)
    viewer = await make_user(x_username=x_username)

    assert await _feed_ids(db_session, viewer) == [post.id]
    assert await feed.get_feed_count(db_session, viewer) == 1


async def test_feed_handle_match_only_applies_to_sponsored_posts(db_session, make_user):
    """A normal post whose cached author handle equals the viewer's is not
    hidden by the sponsor rule — only `user_id` decides ownership there."""
    owner = await make_user(x_username="owner")
    normal = Post(user_id=owner.id, x_link="https://x.com/acme/status/1", tweet_id="1",
                  tweet_author_username="acme", escrow=Decimal("50"),
                  initial_escrow=Decimal("50"), platform="web")
    db_session.add(normal)
    await db_session.commit()
    viewer = await make_user(x_username="acme")

    assert await _feed_ids(db_session, viewer) == [normal.id]
    assert await feed.get_feed_count(db_session, viewer) == 1


async def test_session_start_hides_sponsored_post_from_the_sponsor_account(client, db_session, make_user):
    post = await sponsored_post(db_session)
    sponsor_user = await make_user(x_username="acme")
    other = await make_user(x_username="someone_else")

    r = await client.post("/session/start/", params={"telegram_id": sponsor_user.telegram_id})
    assert r.status_code == 200, r.text
    assert r.json()["posts"] == []

    r = await client.post("/session/start/", params={"telegram_id": other.telegram_id})
    assert r.status_code == 200, r.text
    assert [c["id"] for c in r.json()["posts"]] == [str(post.id)]


async def _engagements(db, user) -> int:
    return await db.scalar(
        select(func.count()).select_from(Engagement).where(Engagement.user_id == user.id)
    )


async def test_click_own_sponsored_post_400(client, db_session, make_user):
    post = await sponsored_post(db_session)
    sponsor_user = await make_user(x_username="ACME")

    r = await client.post(
        "/session/click/", params={"telegram_id": sponsor_user.telegram_id},
        json={"post_id": str(post.id)},
    )

    assert r.status_code == 400, r.text
    assert r.json()["error"] == "Cannot engage with your own post"
    assert await _engagements(db_session, sponsor_user) == 0


async def test_click_sponsored_post_by_other_users_ok(client, db_session, make_user):
    post = await sponsored_post(db_session)
    other = await make_user(x_username="someone_else")
    no_handle = await make_user(x_username=None)

    for user in (other, no_handle):
        r = await client.post(
            "/session/click/", params={"telegram_id": user.telegram_id},
            json={"post_id": str(post.id)},
        )
        assert r.status_code == 200, r.text
        assert r.json()["created"] is True
        assert await _engagements(db_session, user) == 1


# ---------------------------------------------------------------------------
# 2. the platform user is not a person in the admin console
# ---------------------------------------------------------------------------
async def _platform_user(db) -> User:
    platform = await sponsors.platform_user(db)
    await db.commit()
    return platform


async def test_admin_stats_excludes_platform_user(client, db_session, make_user):
    admin = await make_user(role="admin", x_verified=True, is_whitelisted=True)
    platform = await _platform_user(db_session)
    # flag it so a leak would show in those counts too (banned + whitelisted
    # together breaks a constraint, so the whitelisted count relies on total)
    platform.x_verified = True
    platform.is_banned = True
    await db_session.commit()

    r = await client.get("/api/admin/stats/", params={"telegram_id": admin.telegram_id})

    assert r.status_code == 200, r.text
    users = r.json()["users"]
    assert users["total"] == 1
    assert users["by_role"] == {"regular": 0, "admin": 1, "superadmin": 0}
    assert users["banned"] == 0
    assert users["whitelisted"] == 1
    assert users["x_verified"] == 1
    assert users["new_this_week"] == 1
    assert r.json()["credits"]["in_circulation"] == 0.0


async def test_admin_new_users_series_excludes_platform_user(client, db_session, make_user):
    admin = await make_user(role="admin")
    await _platform_user(db_session)

    r = await client.get(
        "/api/admin/stats/timeseries/",
        params={"telegram_id": admin.telegram_id, "metric": "new_users", "days": 7},
    )

    assert r.status_code == 200, r.text
    assert r.json()["total"] == 1.0


async def test_admin_search_users_skips_platform_user(client, db_session, make_user):
    admin = await make_user(role="admin")
    platform = await _platform_user(db_session)
    member = await make_user(x_username="sponsored_fan")

    r = await client.get("/api/admin/users/", params={"telegram_id": admin.telegram_id})
    assert r.status_code == 200, r.text
    # the list endpoint answers {rows, total, limit, offset}
    ids = {row["id"] for row in r.json()["rows"]}
    assert ids == {str(admin.id), str(member.id)}
    assert str(platform.id) not in ids
    assert r.json()["total"] == 2

    # a query can't surface it either
    platform.x_username = "sponsored_bot"
    await db_session.commit()
    r = await client.get(
        "/api/admin/users/", params={"telegram_id": admin.telegram_id, "q": "sponsored"},
    )
    assert r.status_code == 200, r.text
    assert [row["id"] for row in r.json()["rows"]] == [str(member.id)]


@pytest.mark.parametrize("path, body", [
    ("grant-credits/", {"amount": "25"}),
    ("revoke-credits/", {"amount": "10"}),
    ("ban/", {"reason": "spam"}),
    ("unban/", None),
])
async def test_admin_user_ops_refuse_platform_user(client, db_session, make_user, path, body):
    admin = await make_user(role="superadmin")  # revoke is superadmin-only
    platform = await _platform_user(db_session)

    r = await client.post(
        f"/api/admin/users/{platform.id}/{path}",
        params={"telegram_id": admin.telegram_id}, json=body,
    )

    assert r.status_code == 400, r.text
    assert r.json()["error"] == "That's the platform account for sponsored posts"
    await db_session.refresh(platform)
    assert platform.credits == Decimal("0")
    assert platform.is_banned is False


async def test_admin_service_refuses_platform_user_by_string_id(db_session, make_user):
    """The SQLAdmin panel passes primary keys as strings."""
    admin = await make_user(role="admin")
    await _platform_user(db_session)

    with pytest.raises(BadRequest):
        await admin_svc.ban_user(db_session, admin_id=admin.id, user_id=str(PLATFORM_USER_ID))
    with pytest.raises(BadRequest):
        await admin_svc.grant_credits(
            db_session, admin_id=admin.id, user_id=str(PLATFORM_USER_ID), amount=5,
        )
