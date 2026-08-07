"""Integration tests for the /waitlist endpoints, through the ASGI app.
Auth uses ?telegram_id= (the debug bypass); the limiter is off in the fixture."""


def _body(x_link="https://x.com/alice"):
    return {"x_link": x_link}


async def test_register_ok(client):
    r = await client.post(
        "/waitlist/register/", params={"telegram_id": 111}, json=_body()
    )
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "registered"
    assert data["x_username"] == "alice"
    assert data["referral_code"]


async def test_register_idempotent(client):
    await client.post("/waitlist/register/", params={"telegram_id": 111}, json=_body())
    r = await client.post(
        "/waitlist/register/", params={"telegram_id": 111}, json=_body()
    )
    assert r.status_code == 200
    assert r.json()["status"] == "already_registered"


async def test_register_bad_x_link_400(client):
    r = await client.post(
        "/waitlist/register/", params={"telegram_id": 111},
        json=_body(x_link="https://x.com/home"),  # system path, not a username
    )
    assert r.status_code == 400
    assert "error" in r.json()   # spec §4.5 — failures are {"error": ...}, not {"detail": ...}


async def test_register_requires_auth_401(client):
    # no ?telegram_id and no init-data header -> Unauthorized
    r = await client.post("/waitlist/register/", json=_body())
    assert r.status_code == 401


async def test_status_endpoint(client):
    await client.post(
        "/waitlist/register/", params={"telegram_id": 222},
        json=_body(x_link="https://x.com/bob"),
    )
    waitlisted = await client.get("/waitlist/status/", params={"telegram_id": 222})
    assert waitlisted.status_code == 200
    assert waitlisted.json()["status"] == "waitlisted"

    unknown = await client.get("/waitlist/status/", params={"telegram_id": 999})
    assert unknown.json()["status"] == "not_registered"