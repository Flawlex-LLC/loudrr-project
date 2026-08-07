"""Tests for Ch12 — the engagement session (endpoints 9, 10, 11) + the feed,
plus the now-wired /user/ and /user/stats/ counts."""
from decimal import Decimal


from app.models.post import Post
from app.models.site_setting import SiteSetting
from app.repositories.engagement import EngagementRepository
from app.services import site_settings


async def _make_post(db, *, owner_id, escrow="50", status="active", **kw):
    p = Post(
        user_id=owner_id,
        x_link="https://x.com/owner/status/123",
        escrow=Decimal(escrow),
        initial_escrow=Decimal(kw.pop("initial_escrow", escrow)),
        status=status,
        platform="web",
        **kw,
    )
    db.add(p)
    await db.commit()
    return p


# ---- POST /session/start/ ----
async def test_start_empty_feed(client, make_user):
    await make_user(telegram_id=7001)
    r = await client.post("/session/start/", params={"telegram_id": 7001})
    assert r.status_code == 200
    body = r.json()
    assert body["posts"] == []
    assert body["pending_count"] == 0
    assert body["pending_post_ids"] == []
    assert body["show_verification"] is False
    assert body["user"]["daily_cap"] == 100  # seeded by conftest
    assert body["user"]["credits"] == 0.0


async def test_start_returns_feed_post(client, make_user, db_session):
    owner = await make_user(telegram_id=7002, display_name="Owner")
    _viewer = await make_user(telegram_id=7003)
    await _make_post(db_session, owner_id=owner.id, escrow="50")

    r = await client.post("/session/start/", params={"telegram_id": 7003})
    assert r.status_code == 200
    body = r.json()
    assert len(body["posts"]) == 1
    post = body["posts"][0]
    assert post["creator"] == "Owner"
    assert post["escrow_remaining"] == 50.0
    assert post["x_link"].startswith("https://x.com/")
    assert "redirect_url" in post and "/r/" in post["redirect_url"]
    assert post["engagement_progress"] == 0


async def test_start_banned_403(client, make_user):
    await make_user(telegram_id=7004, is_banned=True)
    r = await client.post("/session/start/", params={"telegram_id": 7004})
    assert r.status_code == 403


# ---- POST /session/click/ — edge cases ----
async def test_click_cancelled_post_404(client, make_user, db_session):
    owner = await make_user(telegram_id=7101)
    await make_user(telegram_id=7102)
    post = await _make_post(db_session, owner_id=owner.id, escrow="0", status="cancelled")
    r = await client.post(
        "/session/click/", params={"telegram_id": 7102}, json={"post_id": str(post.id)}
    )
    assert r.status_code == 404


async def test_click_malformed_uuid_422(client, make_user):
    await make_user(telegram_id=7103)
    r = await client.post(
        "/session/click/", params={"telegram_id": 7103}, json={"post_id": "not-a-uuid"}
    )
    assert r.status_code == 422  # rejected by the schema, never reaches the service


# ---- POST /session/click/ ----
async def test_click_creates_engagement(client, make_user, db_session):
    owner = await make_user(telegram_id=7005)
    viewer = await make_user(telegram_id=7006)
    post = await _make_post(db_session, owner_id=owner.id)

    r = await client.post(
        "/session/click/", params={"telegram_id": 7006}, json={"post_id": str(post.id)}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["created"] is True
    assert body["pending_count"] == 1
    assert body["show_verification"] is False  # threshold default 10

    eng = await EngagementRepository(db_session).get(user_id=viewer.id, post_id=post.id)
    assert eng is not None and eng.verified is False and eng.credit_granted is False


async def test_click_idempotent(client, make_user, db_session):
    owner = await make_user(telegram_id=7007)
    await make_user(telegram_id=7008)
    post = await _make_post(db_session, owner_id=owner.id)

    p = {"telegram_id": 7008}
    body1 = (await client.post("/session/click/", params=p, json={"post_id": str(post.id)})).json()
    body2 = (await client.post("/session/click/", params=p, json={"post_id": str(post.id)})).json()
    assert body1["created"] is True
    assert body2["created"] is False
    assert body2["pending_count"] == 1  # still one


async def test_click_own_post_400(client, make_user, db_session):
    owner = await make_user(telegram_id=7009)
    post = await _make_post(db_session, owner_id=owner.id)
    r = await client.post(
        "/session/click/", params={"telegram_id": 7009}, json={"post_id": str(post.id)}
    )
    assert r.status_code == 400


async def test_click_unknown_post_404(client, make_user):
    await make_user(telegram_id=7010)
    import uuid
    r = await client.post(
        "/session/click/", params={"telegram_id": 7010}, json={"post_id": str(uuid.uuid4())}
    )
    assert r.status_code == 404


async def test_click_completed_post_404(client, make_user, db_session):
    owner = await make_user(telegram_id=7011)
    await make_user(telegram_id=7012)
    post = await _make_post(
        db_session, owner_id=owner.id, escrow="0", initial_escrow="50", status="completed"
    )
    r = await client.post(
        "/session/click/", params={"telegram_id": 7012}, json={"post_id": str(post.id)}
    )
    assert r.status_code == 404


# ---- ENGAGEMENT_COOLDOWN (live wiring) ----
# Regression: a >0 ENGAGEMENT_COOLDOWN setting must lock the user out of a
# fresh click for that many seconds after their previous engagement. This is
# the parity-with-Django gap the audit flagged — sessions.record_click now
# reads the setting (default 0 = disabled, current behavior).
async def test_engagement_cooldown_blocks_back_to_back_clicks(
    client, make_user, db_session
):
    db_session.add(SiteSetting(
        key="ENGAGEMENT_COOLDOWN", value="60", data_type="int",
    ))
    await db_session.commit()
    site_settings._cache.clear()

    owner = await make_user(telegram_id=7301)
    await make_user(telegram_id=7302)
    p1 = await _make_post(db_session, owner_id=owner.id)
    p2 = await _make_post(db_session, owner_id=owner.id)
    # first click lands cleanly
    r1 = await client.post(
        "/session/click/", params={"telegram_id": 7302}, json={"post_id": str(p1.id)},
    )
    assert r1.status_code == 200
    # second click within the 60s window must be blocked with a clear message
    r2 = await client.post(
        "/session/click/", params={"telegram_id": 7302}, json={"post_id": str(p2.id)},
    )
    assert r2.status_code == 400
    assert "wait" in r2.json()["error"].lower()


async def test_engagement_cooldown_zero_is_disabled(
    client, make_user, db_session
):
    """The default ENGAGEMENT_COOLDOWN=0 keeps the legacy "fire away" behavior
    so the wiring is safe to roll out without any config flip."""
    db_session.add(SiteSetting(
        key="ENGAGEMENT_COOLDOWN", value="0", data_type="int",
    ))
    await db_session.commit()
    site_settings._cache.clear()

    owner = await make_user(telegram_id=7311)
    await make_user(telegram_id=7312)
    p1 = await _make_post(db_session, owner_id=owner.id)
    p2 = await _make_post(db_session, owner_id=owner.id)
    r1 = await client.post(
        "/session/click/", params={"telegram_id": 7312}, json={"post_id": str(p1.id)},
    )
    r2 = await client.post(
        "/session/click/", params={"telegram_id": 7312}, json={"post_id": str(p2.id)},
    )
    assert r1.status_code == 200 and r2.status_code == 200


async def test_click_show_verification_threshold(client, make_user, db_session):
    # seed a low claim threshold so a single click flips show_verification
    db_session.add(SiteSetting(key="MIN_ENGAGEMENTS_TO_CLAIM", value="1", data_type="int"))
    await db_session.commit()
    site_settings._cache.clear()

    owner = await make_user(telegram_id=7013)
    await make_user(telegram_id=7014)
    post = await _make_post(db_session, owner_id=owner.id)
    r = await client.post(
        "/session/click/", params={"telegram_id": 7014}, json={"post_id": str(post.id)}
    )
    assert r.json()["show_verification"] is True


# ---- POST /session/verify-return/ ----
async def test_verify_return_after_click(client, make_user, db_session):
    owner = await make_user(telegram_id=7015)
    await make_user(telegram_id=7016)
    post = await _make_post(db_session, owner_id=owner.id)
    p = {"telegram_id": 7016}
    await client.post("/session/click/", params=p, json={"post_id": str(post.id)})

    r = await client.post("/session/verify-return/", params=p, json={"post_id": str(post.id)})
    assert r.status_code == 200
    assert r.json()["verified"] is True


async def test_verify_return_no_engagement_404(client, make_user, db_session):
    owner = await make_user(telegram_id=7017)
    await make_user(telegram_id=7018)
    post = await _make_post(db_session, owner_id=owner.id)
    r = await client.post(
        "/session/verify-return/", params={"telegram_id": 7018}, json={"post_id": str(post.id)}
    )
    assert r.status_code == 404


# ---- pending post reappears in start, fresh feed excludes it ----
async def test_clicked_post_moves_to_pending(client, make_user, db_session):
    owner = await make_user(telegram_id=7019)
    await make_user(telegram_id=7020)
    post = await _make_post(db_session, owner_id=owner.id)
    p = {"telegram_id": 7020}
    await client.post("/session/click/", params=p, json={"post_id": str(post.id)})

    body = (await client.post("/session/start/", params=p)).json()
    assert body["pending_count"] == 1
    assert str(post.id) in body["pending_post_ids"]
    assert len(body["posts"]) == 1  # shown as a pending post, not a fresh one


# ---- Ch10 stubs are now real ----
async def test_user_counts_reflect_engagement(client, make_user, db_session):
    owner = await make_user(telegram_id=7021)
    _viewer = await make_user(telegram_id=7022)
    post = await _make_post(db_session, owner_id=owner.id)

    before = (await client.get("/user/", params={"telegram_id": 7022})).json()
    assert before["available_posts"] == 1
    assert before["engaged_today"] == 0

    await client.post("/session/click/", params={"telegram_id": 7022}, json={"post_id": str(post.id)})

    after = (await client.get("/user/", params={"telegram_id": 7022})).json()
    assert after["available_posts"] == 0   # engaged → excluded from feed
    assert after["engaged_today"] == 1

    # owner's stats: 1 post (active), 1 engagement received
    stats = (await client.get("/user/stats/", params={"telegram_id": 7021})).json()
    assert stats["posts"] == {"total": 1, "active": 1, "completed": 0}
    assert stats["engagements"]["received"] == 1
    assert len(stats["recent_posts"]) == 1
