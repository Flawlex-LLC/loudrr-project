"""Integration tests for the /waitlist endpoints, through the ASGI app.
Auth uses ?telegram_id= (the debug bypass); the limiter is off in the fixture."""

from app.core.crypto import sign_x_proof
from app.core.time_utils import utcnow


def _proof(tg_id: int = 111, username: str = "alice", x_user_id: str = "1"):
    return sign_x_proof({
        "tg_id": tg_id,
        "x_username": username,
        "x_user_id": x_user_id,
        "iat": int(utcnow().timestamp()),
    })


def _body(x_proof: str | None = None, **kwargs):
    if x_proof is None:
        x_proof = _proof()
    return {"x_proof": x_proof, **kwargs}


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


async def test_register_missing_x_proof_returns_422(client):
    r = await client.post(
        "/waitlist/register/", params={"telegram_id": 111}, json={},
    )
    assert r.status_code == 422


async def test_register_invalid_x_proof_returns_400(client):
    # Long enough to clear the OAuthProof min_length=32 shape check, but the
    # signature is garbage -> rejected by verify_x_proof with a 400.
    r = await client.post(
        "/waitlist/register/", params={"telegram_id": 111},
        json={"x_proof": "not-a-real-signed-token-just-40-chars-xx"},
    )
    assert r.status_code == 400
    assert "error" in r.json()


async def test_register_too_short_x_proof_returns_422(client):
    # Below OAuthProof's min_length=32 — Pydantic rejects the shape before
    # signature verification even runs.
    r = await client.post(
        "/waitlist/register/", params={"telegram_id": 111},
        json={"x_proof": "short"},
    )
    assert r.status_code == 422


async def test_register_proof_tg_mismatch_returns_400(client):
    # proof signed for tg=999, request auth as tg=111 — must be rejected
    stolen = _proof(tg_id=999, username="bob", x_user_id="9")
    r = await client.post(
        "/waitlist/register/", params={"telegram_id": 111},
        json={"x_proof": stolen},
    )
    assert r.status_code == 400
    assert "different Telegram user" in r.json()["error"]


async def test_register_expired_x_proof_returns_400(client, monkeypatch):
    """Endpoint-level expiry: a proof past its max_age must be rejected at
    the register endpoint (not just in unit tests of verify_x_proof).
    Monkeypatch max_age_seconds=-1 to force the token to be considered stale
    without waiting the real 10 minutes."""
    import app.services.waitlist_x_oauth as svc

    real_verify = svc.verify_x_proof

    def expired_verify(token):
        return real_verify(token, max_age_seconds=-1)

    monkeypatch.setattr(svc, "verify_x_proof", expired_verify)

    r = await client.post(
        "/waitlist/register/", params={"telegram_id": 111}, json=_body()
    )
    assert r.status_code == 400
    assert "error" in r.json()


async def test_register_requires_auth_401(client):
    # no ?telegram_id and no init-data header -> Unauthorized
    r = await client.post("/waitlist/register/", json=_body())
    assert r.status_code == 401


async def test_registered_entry_is_oauth_verified(client, db_session):
    from sqlalchemy import select
    from app.models.waitlist_entry import WaitlistEntry

    r = await client.post(
        "/waitlist/register/", params={"telegram_id": 4242},
        json=_body(x_proof=_proof(tg_id=4242, username="carol", x_user_id="777")),
    )
    assert r.status_code == 200
    row = (
        await db_session.execute(
            select(WaitlistEntry).where(WaitlistEntry.telegram_id == 4242)
        )
    ).scalar_one()
    assert row.x_verified is True
    assert row.x_username == "carol"
    assert row.x_user_id == "777"


async def test_status_endpoint(client):
    await client.post(
        "/waitlist/register/", params={"telegram_id": 222},
        json=_body(x_proof=_proof(tg_id=222, username="bob", x_user_id="2")),
    )
    waitlisted = await client.get("/waitlist/status/", params={"telegram_id": 222})
    assert waitlisted.status_code == 200
    assert waitlisted.json()["status"] == "waitlisted"

    unknown = await client.get("/waitlist/status/", params={"telegram_id": 999})
    assert unknown.json()["status"] == "not_registered"
