"""Tests for app.integrations.twitter (Ch13 — verify reply + cache tweet content).

The whole module is HTTP-bound; we stub httpx.AsyncClient so no real network
traffic happens. Coverage targets:
  * extract_tweet_id — URL parsing edge cases (twitter.com, x.com, fragments, junk)
  * TwitterClient.verify_reply — success/empty/4xx/5xx/timeout/no-key/no-username
  * TwitterClient.get_tweet_content — success, malformed JSON, 404 empty list,
    HTTP error, no-key, media extraction fallbacks
"""
import httpx
import pytest

from app.integrations import twitter as tw
from app.integrations.twitter import (
    TwitterClient,
    extract_tweet_id,
    get_twitter_client,
)


# ---------------------------------------------------------------------------
# helpers — a fake AsyncClient that records the request and returns whatever
# the test sets up. We patch httpx.AsyncClient at the module level.
# ---------------------------------------------------------------------------
class _FakeResponse:
    def __init__(self, status_code=200, json_data=None, text="", raise_json=False):
        self.status_code = status_code
        self._json_data = json_data if json_data is not None else {}
        self.text = text or ""
        self._raise_json = raise_json

    def json(self):
        if self._raise_json:
            raise ValueError("malformed JSON")
        return self._json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            req = httpx.Request("GET", "https://api.twitterapi.io/twitter/x")
            raise httpx.HTTPStatusError(
                f"{self.status_code}", request=req, response=self
            )


class _FakeAsyncClient:
    """Stands in for httpx.AsyncClient. ``script`` is a callable that
    receives (url, headers, params) and returns a _FakeResponse — or raises."""

    def __init__(self, script):
        self._script = script
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, headers=None, params=None):
        self.calls.append({"url": url, "headers": headers, "params": params})
        result = self._script(url, headers, params)
        if isinstance(result, Exception):
            raise result
        return result


def _patch_httpx(monkeypatch, script):
    """Install a fake httpx.AsyncClient and return the instance so the test
    can inspect .calls afterwards."""
    holder = {}

    def _factory(*a, **kw):
        client = _FakeAsyncClient(script)
        holder["client"] = client
        return client

    monkeypatch.setattr(tw.httpx, "AsyncClient", _factory)
    return holder


# ---------------------------------------------------------------------------
# extract_tweet_id
# ---------------------------------------------------------------------------
class TestExtractTweetId:
    def test_twitter_com_url(self):
        assert extract_tweet_id(
            "https://twitter.com/jack/status/20"
        ) == "20"

    def test_x_com_url(self):
        assert extract_tweet_id(
            "https://x.com/elonmusk/status/1234567890123456789"
        ) == "1234567890123456789"

    def test_url_with_query_and_fragment(self):
        assert extract_tweet_id(
            "https://x.com/user/status/999?s=20&t=abc#section"
        ) == "999"

    def test_path_only_status(self):
        # second pattern matches a bare /status/<id>/
        assert extract_tweet_id("/status/42/foo") == "42"

    def test_empty_string_returns_none(self):
        assert extract_tweet_id("") is None

    def test_none_input_returns_none(self):
        assert extract_tweet_id(None) is None  # type: ignore[arg-type]

    def test_garbage_returns_none(self):
        assert extract_tweet_id("https://example.com/foo/bar") is None

    def test_no_numeric_id(self):
        assert extract_tweet_id(
            "https://twitter.com/user/status/notanumber"
        ) is None


# ---------------------------------------------------------------------------
# get_twitter_client / __init__ defaults
# ---------------------------------------------------------------------------
class TestTwitterClientInit:
    def test_explicit_api_key_wins(self):
        c = TwitterClient(api_key="explicit-key")
        assert c.api_key == "explicit-key"

    def test_falls_back_to_settings(self, monkeypatch):
        monkeypatch.setattr(tw.settings, "twitter_api_key", "from-settings")
        c = TwitterClient()
        assert c.api_key == "from-settings"

    def test_empty_string_key_is_kept(self, monkeypatch):
        # An *explicit* "" overrides settings — None is the only sentinel
        # that triggers the settings fallback.
        monkeypatch.setattr(tw.settings, "twitter_api_key", "from-settings")
        c = TwitterClient(api_key="")
        assert c.api_key == ""

    def test_headers_includes_api_key(self):
        c = TwitterClient(api_key="abc")
        assert c._headers() == {"X-API-Key": "abc"}

    def test_get_twitter_client_factory(self, monkeypatch):
        monkeypatch.setattr(tw.settings, "twitter_api_key", "factory-key")
        c = get_twitter_client()
        assert isinstance(c, TwitterClient)
        assert c.api_key == "factory-key"


# ---------------------------------------------------------------------------
# verify_reply
# ---------------------------------------------------------------------------
class TestVerifyReply:
    async def test_no_api_key_returns_skipped_passed(self):
        client = TwitterClient(api_key="")
        result = await client.verify_reply("123", "alice")
        assert result == {
            "passed": True, "reply_verified": True, "like_verified": True,
            "error": None, "skipped": True,
        }

    async def test_missing_username_is_hard_fail(self):
        client = TwitterClient(api_key="key")
        result = await client.verify_reply("123", "")
        assert result["passed"] is False
        assert result["reply_verified"] is False
        assert result["like_verified"] is True
        assert result["skipped"] is False
        assert "no X username" in result["error"]

    async def test_reply_found_passes(self, monkeypatch):
        def script(url, headers, params):
            return _FakeResponse(200, {"tweets": [{"id": "9", "text": "hi"}]})
        holder = _patch_httpx(monkeypatch, script)

        client = TwitterClient(api_key="key")
        result = await client.verify_reply("123", "Alice")

        assert result["passed"] is True
        assert result["reply_verified"] is True
        assert result["skipped"] is False
        assert result["error"] is None
        # username is normalized: lowercased, leading @ stripped
        call = holder["client"].calls[0]
        assert "from:alice" in call["params"]["query"]
        assert "conversation_id:123" in call["params"]["query"]
        assert call["headers"] == {"X-API-Key": "key"}

    async def test_username_at_prefix_stripped(self, monkeypatch):
        def script(url, headers, params):
            return _FakeResponse(200, {"tweets": [{"id": "9"}]})
        holder = _patch_httpx(monkeypatch, script)
        client = TwitterClient(api_key="key")

        await client.verify_reply("123", "@BoB")
        assert "from:bob" in holder["client"].calls[0]["params"]["query"]

    async def test_no_reply_found_fails(self, monkeypatch):
        def script(url, headers, params):
            return _FakeResponse(200, {"tweets": []})
        _patch_httpx(monkeypatch, script)

        client = TwitterClient(api_key="key")
        result = await client.verify_reply("123", "alice")
        assert result["passed"] is False
        assert result["reply_verified"] is False
        assert result["like_verified"] is True
        assert result["skipped"] is False
        assert result["error"] is None

    async def test_empty_response_no_tweets_key(self, monkeypatch):
        def script(url, headers, params):
            return _FakeResponse(200, {})  # missing "tweets"
        _patch_httpx(monkeypatch, script)

        client = TwitterClient(api_key="key")
        result = await client.verify_reply("123", "alice")
        assert result["passed"] is False
        assert result["reply_verified"] is False

    async def test_404_treated_as_passed_skipped(self, monkeypatch):
        def script(url, headers, params):
            return _FakeResponse(404, {}, text="not found")
        _patch_httpx(monkeypatch, script)

        client = TwitterClient(api_key="key")
        result = await client.verify_reply("123", "alice")
        assert result["passed"] is True
        assert result["skipped"] is True
        assert "404" in result["error"]

    async def test_500_treated_as_passed_skipped(self, monkeypatch):
        def script(url, headers, params):
            return _FakeResponse(500, {}, text="boom")
        _patch_httpx(monkeypatch, script)

        client = TwitterClient(api_key="key")
        result = await client.verify_reply("123", "alice")
        assert result["passed"] is True
        assert result["skipped"] is True
        assert "500" in result["error"]

    async def test_network_error_treated_as_passed_skipped(self, monkeypatch):
        def script(url, headers, params):
            return httpx.ConnectError("dns boom")
        _patch_httpx(monkeypatch, script)

        client = TwitterClient(api_key="key")
        result = await client.verify_reply("123", "alice")
        assert result["passed"] is True
        assert result["skipped"] is True
        assert "dns boom" in result["error"]

    async def test_timeout_treated_as_passed_skipped(self, monkeypatch):
        def script(url, headers, params):
            return httpx.TimeoutException("timeout")
        _patch_httpx(monkeypatch, script)

        client = TwitterClient(api_key="key")
        result = await client.verify_reply("123", "alice")
        assert result["passed"] is True
        assert result["skipped"] is True
        assert "timeout" in result["error"]


# ---------------------------------------------------------------------------
# get_tweet_content
# ---------------------------------------------------------------------------
class TestGetTweetContent:
    async def test_no_api_key_returns_none(self):
        client = TwitterClient(api_key="")
        assert await client.get_tweet_content("123") is None

    async def test_success_full_payload(self, monkeypatch):
        def script(url, headers, params):
            return _FakeResponse(200, {
                "tweets": [{
                    "id": 42,
                    "text": "hello world",
                    "createdAt": "2024-01-01T00:00:00Z",
                    "author": {
                        "id": 7,
                        "userName": "alice",
                        "name": "Alice A.",
                        "profilePicture": "https://pbs.twimg.com/p.jpg",
                    },
                    "extendedEntities": {"media": [
                        {"media_url_https": "https://pbs.twimg.com/m1.jpg"},
                        {"url": "https://pbs.twimg.com/m2.jpg"},  # falls back to "url"
                        {"foo": "bar"},  # no usable URL → skipped
                    ]},
                }]
            })
        holder = _patch_httpx(monkeypatch, script)

        client = TwitterClient(api_key="key")
        out = await client.get_tweet_content("42")

        assert out == {
            "tweet_id": "42",
            "text": "hello world",
            "author_id": "7",
            "author_username": "alice",
            "author_name": "Alice A.",
            "author_avatar": "https://pbs.twimg.com/p.jpg",
            "media": ["https://pbs.twimg.com/m1.jpg", "https://pbs.twimg.com/m2.jpg"],
            "created_at": "2024-01-01T00:00:00Z",
        }
        # tweet_ids param wired through
        assert holder["client"].calls[0]["params"] == {"tweet_ids": "42"}

    async def test_empty_tweets_list_returns_none(self, monkeypatch):
        def script(url, headers, params):
            return _FakeResponse(200, {"tweets": []})
        _patch_httpx(monkeypatch, script)

        client = TwitterClient(api_key="key")
        assert await client.get_tweet_content("notfound") is None

    async def test_missing_tweets_key_returns_none(self, monkeypatch):
        def script(url, headers, params):
            return _FakeResponse(200, {})
        _patch_httpx(monkeypatch, script)

        client = TwitterClient(api_key="key")
        assert await client.get_tweet_content("x") is None

    async def test_404_returns_none(self, monkeypatch):
        def script(url, headers, params):
            return _FakeResponse(404, {}, text="not found")
        _patch_httpx(monkeypatch, script)

        client = TwitterClient(api_key="key")
        assert await client.get_tweet_content("123") is None

    async def test_500_returns_none(self, monkeypatch):
        def script(url, headers, params):
            return _FakeResponse(500, {}, text="boom")
        _patch_httpx(monkeypatch, script)

        client = TwitterClient(api_key="key")
        assert await client.get_tweet_content("123") is None

    async def test_network_error_returns_none(self, monkeypatch):
        def script(url, headers, params):
            return httpx.ConnectError("nope")
        _patch_httpx(monkeypatch, script)

        client = TwitterClient(api_key="key")
        assert await client.get_tweet_content("123") is None

    async def test_malformed_json_returns_none(self, monkeypatch):
        # resp.json() raising ValueError isn't an httpx.HTTPError, so the
        # exception currently propagates. Document that contract here.
        def script(url, headers, params):
            return _FakeResponse(200, raise_json=True)
        _patch_httpx(monkeypatch, script)

        client = TwitterClient(api_key="key")
        with pytest.raises(ValueError):
            await client.get_tweet_content("123")

    async def test_defaults_when_fields_missing(self, monkeypatch):
        # tweet present but author absent / id missing — falls back to
        # the tweet_id arg and "" for the strings, [] for media.
        def script(url, headers, params):
            return _FakeResponse(200, {"tweets": [{}]})
        _patch_httpx(monkeypatch, script)

        client = TwitterClient(api_key="key")
        out = await client.get_tweet_content("fallback-id")
        assert out == {
            "tweet_id": "fallback-id",
            "text": "",
            "author_id": "",
            "author_username": "",
            "author_name": "",
            "author_avatar": "",
            "media": [],
            "created_at": "",
        }

    async def test_no_media_entities(self, monkeypatch):
        def script(url, headers, params):
            return _FakeResponse(200, {"tweets": [{
                "id": "9", "text": "t",
                "author": {"id": "1", "userName": "u", "name": "n"},
            }]})
        _patch_httpx(monkeypatch, script)

        client = TwitterClient(api_key="key")
        out = await client.get_tweet_content("9")
        assert out["media"] == []


# ---------------------------------------------------------------------------
# gateway routing — verify LOUDRR_GATEWAY_API takes precedence over TWITTER_API_KEY
# ---------------------------------------------------------------------------
class TestGatewayRouting:
    """The loudrr gateway (gateway.loudrr.com) is a drop-in twitterapi.io-
    compatible service. When LOUDRR_GATEWAY_API is set, TwitterClient must
    route through it INSTEAD of api.twitterapi.io — otherwise we pay for
    twitterapi credits directly instead of going through our own gateway."""

    def test_gateway_selected_when_key_set(self, monkeypatch):
        """LOUDRR_GATEWAY_API set → base_url + api_key come from gateway settings."""
        from app.core.config import settings as app_settings
        monkeypatch.setattr(app_settings, "loudrr_gateway_api", "gw-key-abc")
        monkeypatch.setattr(app_settings, "gateway_base_url", "https://gateway.loudrr.com")
        monkeypatch.setattr(app_settings, "twitter_api_key", "should-not-be-used")

        client = TwitterClient()
        assert client.base_url == "https://gateway.loudrr.com/twitter"
        assert client.api_key == "gw-key-abc"

    def test_falls_back_to_twitterapi_io_when_gateway_key_empty(self, monkeypatch):
        """LOUDRR_GATEWAY_API blank → falls back to twitterapi.io + twitter_api_key.
        Preserves backwards compat for existing deploys that haven't wired the gateway."""
        from app.core.config import settings as app_settings
        monkeypatch.setattr(app_settings, "loudrr_gateway_api", "")
        monkeypatch.setattr(app_settings, "twitter_api_key", "tapi-fallback")

        client = TwitterClient()
        assert client.base_url == "https://api.twitterapi.io/twitter"
        assert client.api_key == "tapi-fallback"

    def test_explicit_api_key_argument_wins(self, monkeypatch):
        """A test/injection-time api_key argument must beat the resolved default
        (both gateway and legacy paths) — preserves pre-gateway behavior."""
        from app.core.config import settings as app_settings
        monkeypatch.setattr(app_settings, "loudrr_gateway_api", "gw-key")

        client = TwitterClient(api_key="explicit-override")
        # base_url still resolves to gateway (gateway_api set), but key wins
        assert client.base_url == "https://gateway.loudrr.com/twitter"
        assert client.api_key == "explicit-override"

    def test_gateway_base_url_trailing_slash_normalized(self, monkeypatch):
        """gateway_base_url with a trailing slash must not produce a double slash
        in the final URL — the /twitter suffix would end up as //twitter."""
        from app.core.config import settings as app_settings
        monkeypatch.setattr(app_settings, "loudrr_gateway_api", "k")
        monkeypatch.setattr(app_settings, "gateway_base_url", "https://gateway.loudrr.com/")

        client = TwitterClient()
        assert client.base_url == "https://gateway.loudrr.com/twitter"
