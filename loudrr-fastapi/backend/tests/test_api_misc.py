"""Integration tests for the non-waitlist endpoints, through the ASGI app."""

from app.models.site_setting import SiteSetting
from app.services import site_settings


async def test_health(client):
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


async def test_security_headers_present(client):
    # every response carries the defensive headers (closes the ZAP findings)
    r = await client.get("/health")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert "content-security-policy" in r.headers
    assert "permissions-policy" in r.headers
    assert r.headers["cross-origin-resource-policy"] == "cross-origin"


async def test_site_settings(client, db_session):
    # the endpoint reads four keys; conftest only seeds DAILY_EARN_CAP, so seed these
    for key, val in [
        ("POST_COST_MIN", "10"), ("POST_COST_MAX", "100"),
        ("POST_COST", "50"), ("CREDIT_PER_ENGAGEMENT", "5"),
    ]:
        db_session.add(SiteSetting(key=key, value=val, data_type="int"))
    await db_session.commit()
    site_settings._cache.clear()

    r = await client.get("/site_settings")
    assert r.status_code == 200
    body = r.json()
    # assert ALL four keys, their values, and their coerced int types
    assert body == {
        "post_cost_min": 10,
        "post_cost_max": 100,
        "post_cost": 50,
        "credit_per_engagement": 5,
    }
    assert all(isinstance(v, int) for v in body.values())  # data_type="int" coercion


async def test_miniapp_settings_endpoint(client, db_session):
    # the frontend's getSettings() hits /settings/ (via /api/miniapp/settings/)
    for key, val in [("POST_COST_MIN", "10"), ("POST_COST_MAX", "100")]:
        db_session.add(SiteSetting(key=key, value=val, data_type="int"))
    await db_session.commit()
    site_settings._cache.clear()

    r = await client.get("/settings/")
    assert r.status_code == 200
    body = r.json()
    assert body["post_cost_min"] == 10
    assert body["post_cost_max"] == 100
    # tier bands ship alongside so edge surfaces (the OG card route) can label
    # a score with the live thresholds instead of a hardcoded copy
    assert isinstance(body["tiers"], list) and body["tiers"]
    assert all({"name", "min_score"} == set(t) for t in body["tiers"])
    # highest threshold first — consumers take the first band a score clears
    mins = [t["min_score"] for t in body["tiers"]]
    assert mins == sorted(mins, reverse=True)
    assert mins[-1] == 0  # Anon is the catch-all


async def test_miniapp_settings_tiers_track_admin_retune(client, db_session):
    """The published bands must follow a runtime retune — otherwise the public
    share card would keep labelling scores with the shipped defaults."""
    from app.services import tier as tier_svc

    # the endpoint reads these too — seed them so it can respond at all
    for key, val in [("POST_COST_MIN", "10"), ("POST_COST_MAX", "100")]:
        db_session.add(SiteSetting(key=key, value=val, data_type="int"))
    await db_session.commit()
    site_settings._cache.clear()

    original = list(tier_svc.TIERS)
    try:
        r = await client.get("/settings/")
        before = {t["name"]: t["min_score"] for t in r.json()["tiers"]}

        # simulate what load_tiers_from_settings does after an admin PUT
        tier_svc.TIERS[:] = [
            (name, 4242 if name == "Based" else min_score, mult)
            for name, min_score, mult in tier_svc.TIERS
        ]
        tier_svc.TIERS.sort(key=lambda t: t[1], reverse=True)

        r2 = await client.get("/settings/")
        after = {t["name"]: t["min_score"] for t in r2.json()["tiers"]}
        assert before["Based"] != after["Based"]
        assert after["Based"] == 4242
    finally:
        tier_svc.TIERS[:] = original


async def test_whoami_with_user(client, make_user):
    await make_user(telegram_id=4242, telegram_username="zoe")
    r = await client.get("/whoami", params={"telegram_id": 4242})  # debug bypass
    assert r.status_code == 200
    assert r.json()["username"] == "zoe"


async def test_whoami_unknown_user_401(client):
    r = await client.get("/whoami", params={"telegram_id": 123456})
    assert r.status_code == 401