"""Tests for the new pre-signup waitlist X OAuth flow.

Covers POST /waitlist/x-oauth/start/ + GET /api/auth/x/callback/waitlist/,
and the itsdangerous proof round-trip. Reuses the _FakeAsyncClient
scaffolding pattern from test_integrations_x_oauth.py to stub the two
outbound HTTP calls (token exchange + /users/me). The browser confirmation
step has its own file: test_waitlist_oauth_confirm.py.
"""
import hashlib
from datetime import timedelta
from urllib.parse import parse_qs, quote_plus, urlparse

import pytest
from sqlalchemy import select

from app.core.crypto import sign_x_proof, verify_x_proof
from app.core.errors import BadRequest
from app.core.time_utils import utcnow
from app.integrations import x_oauth
from app.models.waitlist_oauth_proof import WaitlistOAuthProof
from app.models.waitlist_oauth_state import WaitlistOAuthState
from app.services import waitlist_x_oauth as waitlist_oauth_svc


# --------------------------------------------------------------------------
# Proof round-trip (pure)
# --------------------------------------------------------------------------
def test_proof_signs_and_verifies():
    token = sign_x_proof({"tg_id": 42, "x_username": "alice", "x_user_id": "9"})
    payload = verify_x_proof(token)
    assert payload is not None
    assert payload["tg_id"] == 42
    assert payload["x_username"] == "alice"
    assert payload["x_user_id"] == "9"


def test_proof_tampered_signature_rejected():
    token = sign_x_proof({"tg_id": 1, "x_username": "a", "x_user_id": "1"})
    # flip a character in the signature segment (last segment after final '.')
    parts = token.rsplit(".", 1)
    tampered = parts[0] + "." + ("A" if parts[1][0] != "A" else "B") + parts[1][1:]
    assert verify_x_proof(tampered) is None


def test_proof_expired_rejected():
    token = sign_x_proof({"tg_id": 1, "x_username": "a", "x_user_id": "1"})
    # negative max_age treats any timestamp as expired (itsdangerous compares
    # age > max_age; anything > -1 fails). Cleanly returns None on expiry.
    assert verify_x_proof(token, max_age_seconds=-1) is None


def test_verify_and_extract_rejects_malformed_proof():
    # correctly signed, but the payload is missing x_username — a proof our
    # own callback would never mint. Signature passes, shape check must fail.
    token = sign_x_proof({"tg_id": 7, "x_user_id": "9"})
    with pytest.raises(BadRequest, match="Malformed X OAuth proof"):
        waitlist_oauth_svc._verify_and_extract(token, telegram_id=7)


# --------------------------------------------------------------------------
# _frontend_origin fallback chain
# --------------------------------------------------------------------------
def test_frontend_origin_strips_app_suffix(monkeypatch):
    monkeypatch.setattr(
        waitlist_oauth_svc.settings, "miniapp_url", "https://app.example.com/app"
    )
    monkeypatch.setattr(waitlist_oauth_svc.settings, "site_url", "https://site.example.com")
    assert waitlist_oauth_svc._frontend_origin() == "https://app.example.com"


def test_frontend_origin_falls_back_to_site_url(monkeypatch):
    monkeypatch.setattr(waitlist_oauth_svc.settings, "miniapp_url", "")
    monkeypatch.setattr(waitlist_oauth_svc.settings, "site_url", "https://site.example.com/")
    assert waitlist_oauth_svc._frontend_origin() == "https://site.example.com"


def test_frontend_origin_defaults_to_localhost(monkeypatch):
    monkeypatch.setattr(waitlist_oauth_svc.settings, "miniapp_url", "")
    monkeypatch.setattr(waitlist_oauth_svc.settings, "site_url", "")
    assert waitlist_oauth_svc._frontend_origin() == "http://localhost:3000"


# --------------------------------------------------------------------------
# POST /waitlist/x-oauth/start/
# --------------------------------------------------------------------------
async def test_start_oauth_returns_authorize_url(client, db_session, monkeypatch):
    monkeypatch.setattr(x_oauth.settings, "x_oauth_client_id", "cid")
    monkeypatch.setattr(
        x_oauth.settings,
        "x_oauth_waitlist_callback_url",
        "https://api.example.com/api/auth/x/callback/waitlist/",
    )
    monkeypatch.setattr(x_oauth.settings, "x_oauth_callback_url", "https://api.example.com/cb/")
    r = await client.post(
        "/waitlist/x-oauth/start/", params={"telegram_id": 12345},
    )
    assert r.status_code == 200
    url = r.json()["authorize_url"]
    assert url.startswith(x_oauth.AUTHORIZE_URL + "?")
    # the WAITLIST callback (urlencoded) is what X will redirect to — not the
    # legacy x_oauth_callback_url
    assert (
        "redirect_uri="
        + quote_plus("https://api.example.com/api/auth/x/callback/waitlist/")
    ) in url
    # a state row was persisted keyed to that telegram_id
    rows = (
        await db_session.execute(
            select(WaitlistOAuthState).where(
                WaitlistOAuthState.telegram_id == 12345
            )
        )
    ).scalars().all()
    assert len(rows) == 1
    # ...naming who started it, for the browser confirmation page (the debug
    # identity here; test_waitlist_oauth_confirm.py covers real initData)
    assert rows[0].telegram_label == "@Oxblest (Oxblest)"


async def test_start_oauth_not_configured_503(client, monkeypatch):
    monkeypatch.setattr(x_oauth.settings, "x_oauth_client_id", "")
    monkeypatch.setattr(x_oauth.settings, "x_oauth_waitlist_callback_url", "")
    monkeypatch.setattr(x_oauth.settings, "x_oauth_callback_url", "")
    r = await client.post(
        "/waitlist/x-oauth/start/", params={"telegram_id": 12345},
    )
    assert r.status_code == 503


# --------------------------------------------------------------------------
# GET /api/auth/x/callback/waitlist/
# --------------------------------------------------------------------------
class _FakeResponse:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data or {}
        self.text = text

    def json(self):
        return self._json


class _FakeAsyncClient:
    def __init__(self, *, responses=None, exc=None):
        # responses is a list; each call pops one
        self._responses = list(responses or [])
        self._exc = exc

    def __call__(self, *a, **kw):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, data=None, headers=None):
        if self._exc:
            raise self._exc
        return self._responses.pop(0)

    async def get(self, url, headers=None):
        if self._exc:
            raise self._exc
        return self._responses.pop(0)


def _install_fake_http(monkeypatch, responses):
    fake = _FakeAsyncClient(responses=responses)
    monkeypatch.setattr(x_oauth.httpx, "AsyncClient", fake)
    return fake


async def _seed_state(db_session, *, telegram_id: int, state: str = "s0") -> None:
    db_session.add(
        WaitlistOAuthState(
            state=state,
            telegram_id=telegram_id,
            code_verifier="v" * 43,
            expires_at=utcnow() + timedelta(minutes=5),
        )
    )
    await db_session.commit()


async def test_callback_valid_state_stores_unconfirmed_proof_and_302s_to_confirm(client, db_session, monkeypatch):
    monkeypatch.setattr(x_oauth.settings, "x_oauth_client_id", "cid")
    monkeypatch.setattr(x_oauth.settings, "x_oauth_client_secret", "sec")
    monkeypatch.setattr(x_oauth.settings, "x_oauth_callback_url", "https://cb/")
    monkeypatch.setattr(x_oauth.settings, "x_oauth_waitlist_callback_url", "https://cb/w/")
    monkeypatch.setattr(x_oauth.settings, "miniapp_url", "https://app.example.com/app")

    await _seed_state(db_session, telegram_id=555, state="good-state")

    _install_fake_http(monkeypatch, responses=[
        _FakeResponse(200, {"access_token": "tok"}),
        _FakeResponse(200, {"data": {"id": "999", "username": "alice", "name": "Alice"}}),
    ])

    r = await client.get(
        "/api/auth/x/callback/waitlist/",
        params={"code": "the-code", "state": "good-state"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    # the browser goes to the confirmation page with a one-time token; the
    # proof never travels in the browser URL (history, logs, analytics)
    location = urlparse(r.headers["location"])
    assert (location.scheme, location.netloc, location.path) == (
        "https", "app.example.com", "/waitlist/oauth-return",
    )
    # the one-time token rides the FRAGMENT, which browsers never send to a
    # server (no access log, no Referer, no analytics tag)
    assert not location.query, r.headers["location"]
    fragment = parse_qs(location.fragment)
    assert list(fragment) == ["confirm"]
    token = fragment["confirm"][0]
    assert len(token) >= 43
    stored = await db_session.get(WaitlistOAuthProof, 555)
    assert stored.proof not in r.headers["location"]
    payload = verify_x_proof(stored.proof)
    assert payload["tg_id"] == 555
    assert payload["x_username"] == "alice"
    assert payload["x_user_id"] == "999"
    assert stored.error is None
    # stored UNCONFIRMED, holding only the token's hash
    assert stored.confirmed_at is None
    assert stored.confirm_token_hash == hashlib.sha256(token.encode()).hexdigest()
    # a state row from before labels existed falls back to the Telegram id
    assert stored.telegram_label == "Telegram user 555"

    # state row consumed
    remaining = (
        await db_session.execute(
            select(WaitlistOAuthState).where(WaitlistOAuthState.state == "good-state")
        )
    ).scalar_one_or_none()
    assert remaining is None


async def test_callback_user_denies_302s_with_error(client, monkeypatch):
    monkeypatch.setattr(x_oauth.settings, "miniapp_url", "https://app.example.com/app")
    r = await client.get(
        "/api/auth/x/callback/waitlist/",
        params={"error": "access_denied"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert "error=denied" in r.headers["location"]


async def test_callback_missing_state_302s_invalid(client, monkeypatch):
    monkeypatch.setattr(x_oauth.settings, "miniapp_url", "https://app.example.com/app")
    r = await client.get(
        "/api/auth/x/callback/waitlist/", follow_redirects=False,
    )
    assert r.status_code == 302
    assert "error=invalid" in r.headers["location"]


async def test_callback_unknown_state_302s_expired(client, monkeypatch):
    monkeypatch.setattr(x_oauth.settings, "miniapp_url", "https://app.example.com/app")
    r = await client.get(
        "/api/auth/x/callback/waitlist/",
        params={"code": "c", "state": "nope"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert "error=expired" in r.headers["location"]


async def test_callback_token_exchange_fails_302s_error(client, db_session, monkeypatch):
    monkeypatch.setattr(x_oauth.settings, "x_oauth_client_id", "cid")
    monkeypatch.setattr(x_oauth.settings, "x_oauth_client_secret", "sec")
    monkeypatch.setattr(x_oauth.settings, "x_oauth_callback_url", "https://cb/")
    monkeypatch.setattr(x_oauth.settings, "x_oauth_waitlist_callback_url", "https://cb/w/")
    monkeypatch.setattr(x_oauth.settings, "miniapp_url", "https://app.example.com/app")
    await _seed_state(db_session, telegram_id=1, state="tokfail")
    _install_fake_http(monkeypatch, responses=[
        _FakeResponse(400, {"error": "bad_grant"}, text="bad"),
    ])
    r = await client.get(
        "/api/auth/x/callback/waitlist/",
        params={"code": "c", "state": "tokfail"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert "error=token" in r.headers["location"]


# --------------------------------------------------------------------------
# GET /waitlist/x-oauth/proof/ — server-side proof handoff for Telegram WebView
# --------------------------------------------------------------------------
EMPTY_POLL = {
    "proof": None, "x_username": None, "expires_in": None, "error": None,
    "awaiting_confirmation": False,
}


async def test_proof_poll_no_row_returns_null(client):
    r = await client.get("/waitlist/x-oauth/proof/", params={"telegram_id": 555})
    assert r.status_code == 200
    assert r.json() == EMPTY_POLL


async def test_proof_poll_returns_proof_until_registration(client, db_session, monkeypatch):
    """The poll is NOT destructive: a dropped response or a WebView restart
    must not cost the applicant another trip to X. It also carries the
    verified handle, so the frontend never decodes the (compressed) token."""
    monkeypatch.setattr(x_oauth.settings, "x_oauth_client_id", "cid")
    monkeypatch.setattr(x_oauth.settings, "x_oauth_client_secret", "sec")
    monkeypatch.setattr(x_oauth.settings, "x_oauth_callback_url", "https://cb/")
    monkeypatch.setattr(x_oauth.settings, "x_oauth_waitlist_callback_url", "https://cb/w/")
    monkeypatch.setattr(x_oauth.settings, "miniapp_url", "https://app.example.com/app")

    await _seed_state(db_session, telegram_id=555, state="handoff-state")
    _install_fake_http(monkeypatch, responses=[
        _FakeResponse(200, {"access_token": "tok"}),
        _FakeResponse(200, {"data": {"id": "999", "username": "alice", "name": "Alice"}}),
    ])
    r = await client.get(
        "/api/auth/x/callback/waitlist/",
        params={"code": "the-code", "state": "handoff-state"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    # withheld until the browser that authorized confirms the link
    waiting = (await client.get("/waitlist/x-oauth/proof/", params={"telegram_id": 555})).json()
    assert waiting == EMPTY_POLL | {"awaiting_confirmation": True}
    token = parse_qs(urlparse(r.headers["location"]).fragment)["confirm"][0]
    confirmed = await client.post("/waitlist/x-oauth/confirm/", json={"token": token, "decision": "confirm"})
    assert confirmed.status_code == 200

    r1 = (await client.get("/waitlist/x-oauth/proof/", params={"telegram_id": 555})).json()
    assert verify_x_proof(r1["proof"])["tg_id"] == 555
    assert r1["x_username"] == "alice"
    assert 590 <= r1["expires_in"] <= 600
    assert r1["error"] is None
    assert r1["awaiting_confirmation"] is False
    # still there on the next poll
    r2 = (await client.get("/waitlist/x-oauth/proof/", params={"telegram_id": 555})).json()
    assert r2["proof"] == r1["proof"]
    # another Telegram user never sees it
    other = (await client.get("/waitlist/x-oauth/proof/", params={"telegram_id": 556})).json()
    assert other == EMPTY_POLL


async def test_proof_poll_stale_row_reports_expired_once(client, db_session):
    # a row older than PROOF_TTL_SECONDS is dead — the register endpoint
    # would reject the token anyway, so the poll must not hand it out
    db_session.add(WaitlistOAuthProof(
        telegram_id=777,
        proof="stale-proof-token",
        created_at=utcnow() - timedelta(seconds=601),
    ))
    await db_session.commit()
    r = await client.get("/waitlist/x-oauth/proof/", params={"telegram_id": 777})
    assert r.status_code == 200
    assert r.json() == EMPTY_POLL | {"error": "expired"}
    r = await client.get("/waitlist/x-oauth/proof/", params={"telegram_id": 777})
    assert r.json() == EMPTY_POLL


async def test_denied_on_x_reaches_the_polling_mini_app(client, db_session, monkeypatch):
    """The failure used to live only in the SYSTEM browser's URL, leaving the
    mini-app on "Waiting for X…" forever."""
    monkeypatch.setattr(x_oauth.settings, "miniapp_url", "https://app.example.com/app")
    await _seed_state(db_session, telegram_id=901, state="deny-state")
    r = await client.get(
        "/api/auth/x/callback/waitlist/",
        params={"error": "access_denied", "state": "deny-state"},
        follow_redirects=False,
    )
    assert "error=denied" in r.headers["location"]
    polled = (await client.get("/waitlist/x-oauth/proof/", params={"telegram_id": 901})).json()
    assert polled == EMPTY_POLL | {"error": "denied"}
    # reported once — the next Connect X starts clean
    again = (await client.get("/waitlist/x-oauth/proof/", params={"telegram_id": 901})).json()
    assert again == EMPTY_POLL


async def test_expired_state_reaches_its_mini_app(client, db_session, monkeypatch):
    monkeypatch.setattr(x_oauth.settings, "miniapp_url", "https://app.example.com/app")
    db_session.add(WaitlistOAuthState(
        state="old-state", telegram_id=902, code_verifier="v" * 43,
        expires_at=utcnow() - timedelta(minutes=1),
    ))
    await db_session.commit()
    r = await client.get(
        "/api/auth/x/callback/waitlist/",
        params={"code": "c", "state": "old-state"},
        follow_redirects=False,
    )
    assert "error=expired" in r.headers["location"]
    polled = (await client.get("/waitlist/x-oauth/proof/", params={"telegram_id": 902})).json()
    assert polled["error"] == "expired"


async def test_callback_fetch_me_fails_302s_error(client, db_session, monkeypatch):
    monkeypatch.setattr(x_oauth.settings, "x_oauth_client_id", "cid")
    monkeypatch.setattr(x_oauth.settings, "x_oauth_client_secret", "sec")
    monkeypatch.setattr(x_oauth.settings, "x_oauth_callback_url", "https://cb/")
    monkeypatch.setattr(x_oauth.settings, "x_oauth_waitlist_callback_url", "https://cb/w/")
    monkeypatch.setattr(x_oauth.settings, "miniapp_url", "https://app.example.com/app")
    await _seed_state(db_session, telegram_id=1, state="mefail")
    _install_fake_http(monkeypatch, responses=[
        _FakeResponse(200, {"access_token": "tok"}),
        _FakeResponse(500, {}, text="upstream"),
    ])
    r = await client.get(
        "/api/auth/x/callback/waitlist/",
        params={"code": "c", "state": "mefail"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert "error=profile" in r.headers["location"]
