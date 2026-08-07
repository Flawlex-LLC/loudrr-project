"""Unit tests for app.integrations.x_oauth.

Covers PKCE/state helpers (pure), authorize URL construction, configuration
detection, and the two async HTTP endpoints (token exchange + /users/me).
httpx is mocked the same way the other integration tests do — by replacing
``httpx.AsyncClient`` in the module with a fake context-manager that returns
a stub response (or raises an ``httpx.HTTPError``) per test.
"""
import base64
import hashlib
import string
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.integrations import x_oauth


# --------------------------------------------------------------------------
# Tiny fakes for httpx
# --------------------------------------------------------------------------
class _FakeResponse:
    def __init__(self, status_code: int = 200, json_data=None, text: str = ""):
        self.status_code = status_code
        self._json = json_data if json_data is not None else {}
        self.text = text

    def json(self):
        return self._json


class _FakeAsyncClient:
    """Context-manager stand-in for ``httpx.AsyncClient``.

    Records the last call so a test can introspect URL/body/headers, and
    either returns a pre-built response or raises a pre-built exception.
    """

    def __init__(self, *, response=None, exc=None):
        self._response = response
        self._exc = exc
        self.last_post = None
        self.last_get = None

    def __call__(self, *args, **kwargs):
        # ``httpx.AsyncClient(timeout=...)`` — we ignore the args
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, data=None, headers=None):
        self.last_post = {"url": url, "data": data, "headers": headers}
        if self._exc is not None:
            raise self._exc
        return self._response

    async def get(self, url, headers=None):
        self.last_get = {"url": url, "headers": headers}
        if self._exc is not None:
            raise self._exc
        return self._response


def _install_fake_client(monkeypatch, *, response=None, exc=None) -> _FakeAsyncClient:
    fake = _FakeAsyncClient(response=response, exc=exc)
    # ``async with httpx.AsyncClient(timeout=_TIMEOUT) as client`` —
    # replacing the class on the module's ``httpx`` reference is enough,
    # since the module does ``import httpx`` (not ``from httpx import ...``).
    monkeypatch.setattr(x_oauth.httpx, "AsyncClient", fake)
    return fake


# --------------------------------------------------------------------------
# new_state — pure
# --------------------------------------------------------------------------
def test_new_state_returns_urlsafe_string():
    s = x_oauth.new_state()
    assert isinstance(s, str)
    # token_urlsafe(32) → 32 random bytes → base64url with no padding → ~43 chars
    assert 40 <= len(s) <= 48
    allowed = set(string.ascii_letters + string.digits + "-_")
    assert set(s) <= allowed


def test_new_state_is_unique_per_call():
    # 100 draws collision-free is effectively certain for 256-bit tokens
    samples = {x_oauth.new_state() for _ in range(100)}
    assert len(samples) == 100


# --------------------------------------------------------------------------
# make_pkce — pure
# --------------------------------------------------------------------------
def test_make_pkce_verifier_is_base64url():
    verifier, _challenge = x_oauth.make_pkce()
    assert isinstance(verifier, str)
    # 48 bytes → base64url no-pad → 64 chars
    assert len(verifier) == 64
    allowed = set(string.ascii_letters + string.digits + "-_")
    assert set(verifier) <= allowed
    # no padding
    assert "=" not in verifier


def test_make_pkce_challenge_is_sha256_of_verifier():
    verifier, challenge = x_oauth.make_pkce()
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
        .rstrip(b"=")
        .decode("ascii")
    )
    assert challenge == expected
    # SHA-256 → 32 bytes → base64url no-pad → 43 chars
    assert len(challenge) == 43
    assert "=" not in challenge


def test_make_pkce_is_random_per_call():
    a, _ = x_oauth.make_pkce()
    b, _ = x_oauth.make_pkce()
    assert a != b


# --------------------------------------------------------------------------
# build_authorize_url
# --------------------------------------------------------------------------
def test_build_authorize_url_contains_all_oauth_params(monkeypatch):
    monkeypatch.setattr(x_oauth.settings, "x_oauth_client_id", "test-client-id")
    monkeypatch.setattr(
        x_oauth.settings, "x_oauth_callback_url", "https://app.example.com/cb"
    )
    url = x_oauth.build_authorize_url("the-state", "the-challenge")
    assert url.startswith(x_oauth.AUTHORIZE_URL + "?")
    qs = parse_qs(urlparse(url).query)
    assert qs["client_id"] == ["test-client-id"]
    assert qs["state"] == ["the-state"]
    assert qs["code_challenge"] == ["the-challenge"]
    assert qs["code_challenge_method"] == ["S256"]
    assert qs["redirect_uri"] == ["https://app.example.com/cb"]
    assert qs["scope"] == [x_oauth.SCOPES]
    assert qs["response_type"] == ["code"]


def test_build_authorize_url_raises_if_unconfigured(monkeypatch):
    monkeypatch.setattr(x_oauth.settings, "x_oauth_client_id", "")
    with pytest.raises(RuntimeError):
        x_oauth.build_authorize_url("s", "c")


# --------------------------------------------------------------------------
# is_configured
# --------------------------------------------------------------------------
def test_is_configured_true_when_both_set(monkeypatch):
    monkeypatch.setattr(x_oauth.settings, "x_oauth_client_id", "cid")
    monkeypatch.setattr(x_oauth.settings, "x_oauth_callback_url", "https://x/cb")
    assert x_oauth.is_configured() is True


def test_is_configured_false_when_client_id_missing(monkeypatch):
    monkeypatch.setattr(x_oauth.settings, "x_oauth_client_id", "")
    monkeypatch.setattr(x_oauth.settings, "x_oauth_callback_url", "https://x/cb")
    assert x_oauth.is_configured() is False


def test_is_configured_false_when_callback_missing(monkeypatch):
    monkeypatch.setattr(x_oauth.settings, "x_oauth_client_id", "cid")
    monkeypatch.setattr(x_oauth.settings, "x_oauth_callback_url", "")
    assert x_oauth.is_configured() is False


# --------------------------------------------------------------------------
# exchange_code_for_token
# --------------------------------------------------------------------------
async def test_exchange_code_for_token_success(monkeypatch):
    monkeypatch.setattr(x_oauth.settings, "x_oauth_client_id", "cid")
    monkeypatch.setattr(x_oauth.settings, "x_oauth_client_secret", "sec")
    monkeypatch.setattr(
        x_oauth.settings, "x_oauth_callback_url", "https://app/cb"
    )
    fake = _install_fake_client(
        monkeypatch,
        response=_FakeResponse(200, {"access_token": "tok-123"}),
    )
    token = await x_oauth.exchange_code_for_token("auth-code", "verifier")
    assert token == "tok-123"
    # sanity: hit the documented token endpoint with our form body
    assert fake.last_post is not None
    assert fake.last_post["url"] == x_oauth.TOKEN_URL
    assert fake.last_post["data"]["code"] == "auth-code"
    assert fake.last_post["data"]["code_verifier"] == "verifier"
    assert fake.last_post["data"]["grant_type"] == "authorization_code"
    assert fake.last_post["headers"]["Authorization"].startswith("Basic ")


async def test_exchange_code_for_token_returns_none_on_4xx(monkeypatch):
    _install_fake_client(
        monkeypatch, response=_FakeResponse(400, {"error": "invalid_grant"}, "bad")
    )
    assert await x_oauth.exchange_code_for_token("c", "v") is None


async def test_exchange_code_for_token_returns_none_on_5xx(monkeypatch):
    _install_fake_client(
        monkeypatch, response=_FakeResponse(503, {}, "upstream down")
    )
    assert await x_oauth.exchange_code_for_token("c", "v") is None


async def test_exchange_code_for_token_returns_none_on_network_error(monkeypatch):
    _install_fake_client(monkeypatch, exc=httpx.ConnectError("boom"))
    assert await x_oauth.exchange_code_for_token("c", "v") is None


async def test_exchange_code_for_token_returns_none_when_body_missing_token(monkeypatch):
    # 200 OK but no access_token in payload — .get(...) returns None
    _install_fake_client(monkeypatch, response=_FakeResponse(200, {}))
    assert await x_oauth.exchange_code_for_token("c", "v") is None


# --------------------------------------------------------------------------
# fetch_me
# --------------------------------------------------------------------------
async def test_fetch_me_success(monkeypatch):
    user = {"id": "111", "username": "alice", "name": "Alice"}
    fake = _install_fake_client(
        monkeypatch, response=_FakeResponse(200, {"data": user})
    )
    result = await x_oauth.fetch_me("access-tok")
    assert result == user
    assert fake.last_get["url"] == x_oauth.USERS_ME_URL
    assert fake.last_get["headers"]["Authorization"] == "Bearer access-tok"


async def test_fetch_me_returns_none_when_data_missing(monkeypatch):
    # 200 OK, but no "data" key — .get("data") is None → falsy → returns None
    _install_fake_client(monkeypatch, response=_FakeResponse(200, {}))
    assert await x_oauth.fetch_me("tok") is None


async def test_fetch_me_returns_none_on_4xx(monkeypatch):
    _install_fake_client(
        monkeypatch, response=_FakeResponse(401, {}, "unauthorized")
    )
    assert await x_oauth.fetch_me("tok") is None


async def test_fetch_me_returns_none_on_5xx(monkeypatch):
    _install_fake_client(monkeypatch, response=_FakeResponse(500, {}, "boom"))
    assert await x_oauth.fetch_me("tok") is None


async def test_fetch_me_returns_none_on_network_error(monkeypatch):
    _install_fake_client(monkeypatch, exc=httpx.ReadTimeout("slow"))
    assert await x_oauth.fetch_me("tok") is None


# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------
def test_state_ttl_seconds_is_positive_int():
    assert isinstance(x_oauth.STATE_TTL_SECONDS, int)
    assert x_oauth.STATE_TTL_SECONDS > 0
