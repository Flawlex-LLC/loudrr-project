"""Tests for app.integrations.sorsa (public-page score provider) and the
SCORE_PROVIDER switch.

No network: pages are synthetic Next.js flight payloads built the way
app.sorsa.io serves them (JSON-string pushes of compact JSON), and every HTTP
call goes through an httpx.MockTransport injected via client_factory.
Covered: page + followers-info parsing, the provider payload shape, caching,
the pushback circuit breaker, dead-proxy retry, the no-proxy refusal, request
pacing, and proxy-pool loading.
"""
import asyncio
import json
import time

import httpx
import pytest

from app.core.config import settings
from app.integrations import sorsa
from app.integrations.sorsa import SorsaClient, parse_followers_info, parse_profile_page

SORSA_ID = "d3646a57-3939-422a-82d7-fb9947d0af3e"

ACCOUNT = {
    "id": SORSA_ID,
    "screen_name": "0xBlest_",
    "name": "Blest",
    # braces + quotes inside strings must not confuse the object scan
    "description": 'building {scalable} web3 systems "for real"',
    "avatar": "https://pbs.twimg.com/profile_images/1/a.jpg",
    "banner": "https://pbs.twimg.com/profile_banners/1456366493323644928/1744714063",
    "register_date": 1636059824,
    "followers": 9251,
    "friends": 3042,
    "tweets": 62793,
    "in_watchlist": False,
    "category": "influencer",
    "score_value": 587.22144,
    "score_delta": -1.5272367,
    "bot_followers": {"status": "no_process", "value": 0},
    "tags": ["Influencer"],
}

FOLLOWERS_INFO = {
    "followers": {
        "influencer": {
            "accounts": [
                {"name": "Thread Guy", "screen_name": "notthreadguy", "avatar": "a1", "score_value": 3756.2, "followers_value": 1, "tags": []},
                {"name": "Mario", "screen_name": "MarioNawfal", "avatar": "a2", "score_value": 2936.0, "followers_value": 1, "tags": []},
            ],
            "total": 572,
        },
        "project": {
            "accounts": [
                {"name": "Anon Chain", "screen_name": "anonchain", "avatar": "a3", "score_value": 2767, "followers_value": 1, "tags": []},
                # the same account listed twice across categories -> kept once
                {"name": "Mario", "screen_name": "marionawfal", "avatar": "a2", "score_value": 2936.0, "followers_value": 1, "tags": []},
            ],
            "total": 44,
        },
        "fund": {"accounts": [], "total": 2, "total_pagination": 0},
    },
    "followers_scores": {"total": 664, "scores": {"<250": 35}},
}


def _compact(obj) -> str:
    return json.dumps(obj, separators=(",", ":"))


def _flight_page(*chunks: str) -> str:
    """HTML whose flight data is `chunks`, each pushed as a JS string literal."""
    scripts = "".join(f"<script>self.__next_f.push([1,{json.dumps(c)}])</script>" for c in chunks)
    return f"<!DOCTYPE html><html><head><title>t</title></head><body>{scripts}</body></html>"


def _profile_page(account=ACCOUNT) -> str:
    line = '5:["$","$L1a",null,{"account":' + _compact(account) + ',"recommended":[' + _compact(
        {"name": "Other", "screen_name": "someone_else", "score_value": 900}
    ) + "]}]\n"
    # split mid-object across two pushes, like a streamed page
    return _flight_page(line[:120], line[120:])


NOT_INDEXED_PAGE = (
    "<html><body>Sign in to dig deeper This account isn&#x27;t in our base yet. "
    "Log in to pull it live from X.</body></html>"
)


@pytest.fixture(autouse=True)
def _fresh_sorsa_state():
    sorsa.reset_state()
    yield
    sorsa.reset_state()


class _Recorder:
    """client_factory that routes every request to `handler(request, proxy)`
    and records (proxy, url, monotonic time)."""

    def __init__(self, handler):
        self.handler = handler
        self.calls: list[tuple[str, str, float]] = []

    def __call__(self, proxy):
        def _handle(request):
            self.calls.append((proxy, str(request.url), time.monotonic()))
            return self.handler(request, proxy)

        return httpx.AsyncClient(transport=httpx.MockTransport(_handle))


NOT_INDEXED_API = {"error": "please first login"}


def _happy(request, proxy):
    """Sorsa as observed live: the search API knows the account, followers-info answers."""
    if request.url.path == "/v1/accounts/search":
        return httpx.Response(200, json=ACCOUNT)
    if request.url.path.endswith("/followers-info"):
        return httpx.Response(200, json=FOLLOWERS_INFO)
    return httpx.Response(404)


def _client(recorder, proxies=("http://u:p@1.1.1.1:80", "http://u:p@2.2.2.2:80"), interval=0.0):
    return SorsaClient(list(proxies), client_factory=recorder, min_interval_s=interval)


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------
def test_parse_profile_page_finds_the_account_across_chunks():
    acc = parse_profile_page(_profile_page(), "0xblest_")  # case-insensitive
    assert acc is not None
    assert acc["score_value"] == 587.22144
    assert acc["description"] == 'building {scalable} web3 systems "for real"'


def test_parse_profile_page_is_key_order_independent():
    # a nested object BEFORE screen_name: the walk-back must skip past it
    reordered = {"bot_followers": {"value": 1}, "score_value": 12.5,
                 "screen_name": "0xBlest_", "id": SORSA_ID, "followers": 3}
    page = _flight_page('3:["$","div",null,' + _compact({"x": {"nested": reordered}}) + "]\n")
    assert parse_profile_page(page, "0xBlest_")["score_value"] == 12.5


def test_parse_profile_page_ignores_other_accounts_and_unscored_objects():
    assert parse_profile_page(_profile_page(), "someone_else_x") is None
    unscored = dict(ACCOUNT)
    del unscored["score_value"]
    assert parse_profile_page(_profile_page(unscored), "0xBlest_") is None
    assert parse_profile_page(NOT_INDEXED_PAGE, "0xBlest_") is None
    assert parse_profile_page("<html>garbage { \"screen_name\": </html>", "0xBlest_") is None


def test_parse_followers_info_totals_and_top_best_first():
    total, top = parse_followers_info(FOLLOWERS_INFO)
    assert total == 572 + 44 + 2
    assert [a["username"] for a in top] == ["notthreadguy", "MarioNawfal", "anonchain"]
    assert top[0] == {"username": "notthreadguy", "name": "Thread Guy", "avatar": "a1", "score": 3756.2}


@pytest.mark.parametrize("payload", [None, [], {}, {"followers": "nope"}])
def test_parse_followers_info_unrecognised(payload):
    assert parse_followers_info(payload) == (None, [])


def test_parse_proxies_accepts_webshare_lines_and_urls():
    raw = "1.2.3.4:8080:user:p@ss\nhttp://a:b@5.6.7.8:3128 , 9.9.9.9:1:u:p;bad-line"
    assert sorsa.parse_proxies(raw) == [
        "http://user:p%40ss@1.2.3.4:8080",
        "http://a:b@5.6.7.8:3128",
        "http://u:p@9.9.9.9:1",
    ]


def test_load_proxy_pool_prefers_env_over_file(monkeypatch, tmp_path):
    f = tmp_path / "proxies.txt"
    f.write_text("7.7.7.7:1:u:p\n", encoding="utf-8")
    monkeypatch.setattr(settings, "scrape_proxy_file", str(f))
    monkeypatch.setattr(settings, "scrape_proxies", "")
    assert sorsa.load_proxy_pool() == ["http://u:p@7.7.7.7:1"]
    monkeypatch.setattr(settings, "scrape_proxies", "8.8.8.8:2:u:p")
    assert sorsa.load_proxy_pool() == ["http://u:p@8.8.8.8:2"]
    monkeypatch.setattr(settings, "scrape_proxies", "")
    monkeypatch.setattr(settings, "scrape_proxy_file", str(tmp_path / "missing.txt"))
    assert sorsa.load_proxy_pool() == []


# ---------------------------------------------------------------------------
# client
# ---------------------------------------------------------------------------
async def test_get_user_data_returns_provider_payload():
    rec = _Recorder(_happy)
    client = _client(rec)
    data = await client.get_user_data("@0xBlest_")

    assert client.last_outcome == "found"
    assert data["score"] == 587.22144
    assert data["id"] == "1456366493323644928"      # from the banner URL
    assert data["screen_name"] == "0xBlest_"
    assert data["followers_count"] == 9251
    assert data["friends_count"] == 3042
    assert data["register_date"] == "2021-11-04"
    assert data["smart_followers"] == 618
    assert [a["username"] for a in data["top_followers"]] == ["notthreadguy", "MarioNawfal", "anonchain"]
    # the paywalled bot stats are never surfaced
    assert "bot_followers" not in data
    assert [c[1] for c in rec.calls] == [
        "https://api.sorsa.io/v1/accounts/search?query=0xBlest_",
        f"https://api.sorsa.io/v1/accounts/{SORSA_ID}/followers-info",
    ]


async def test_top_followers_reuse_the_cached_fetch():
    rec = _Recorder(_happy)
    client = _client(rec)
    await client.get_user_data("0xBlest_")
    top = await client.get_top_followers("0XBLEST_", k=2)
    assert [a["username"] for a in top] == ["notthreadguy", "MarioNawfal"]
    assert client.last_outcome == "found"
    assert len(rec.calls) == 2  # no second scrape


async def test_not_indexed_account_is_none_and_cached():
    rec = _Recorder(lambda req, proxy: httpx.Response(400, json=NOT_INDEXED_API))
    client = _client(rec)
    assert await client.get_user_data("smallfry") is None
    assert client.last_outcome == "not_indexed" and client.is_unavailable() is False
    assert await client.get_top_followers("smallfry") is None
    assert client.last_outcome == "not_indexed"
    assert len(rec.calls) == 1


async def test_exact_match_api_answering_for_another_handle_is_not_indexed():
    other = dict(ACCOUNT, screen_name="0xBlest")  # a different real account
    rec = _Recorder(lambda req, proxy: httpx.Response(200, json=other))
    client = _client(rec)
    assert await client.get_user_data("0xBlest_") is None
    assert client.last_outcome == "not_indexed"


@pytest.mark.parametrize("api_response", [
    httpx.Response(500, text="oops"),
    httpx.Response(200, text="<html>not json</html>"),
])
async def test_unexpected_api_answer_falls_back_to_the_public_page(api_response):
    def handler(request, proxy):
        if request.url.path == "/v1/accounts/search":
            return api_response
        if request.url.host == "app.sorsa.io":
            return httpx.Response(200, text=_profile_page())
        if request.url.path.endswith("/followers-info"):
            return httpx.Response(200, json=FOLLOWERS_INFO)
        return httpx.Response(404)

    rec = _Recorder(handler)
    client = _client(rec)
    data = await client.get_user_data("0xBlest_")
    assert data["score"] == 587.22144
    assert client.last_outcome == "found"
    assert "https://app.sorsa.io/profile/0xBlest_" in [c[1] for c in rec.calls]


async def test_page_fallback_not_indexed_marker():
    def handler(request, proxy):
        if request.url.path == "/v1/accounts/search":
            return httpx.Response(502, text="bad gateway")
        return httpx.Response(200, text=NOT_INDEXED_PAGE)

    client = _client(_Recorder(handler))
    assert await client.get_user_data("smallfry") is None
    assert client.last_outcome == "not_indexed"


@pytest.mark.parametrize("response", [
    httpx.Response(403, text="<title>Just a moment...</title>", headers={"cf-mitigated": "challenge"}),
    httpx.Response(429, text="slow down"),
])
async def test_pushback_opens_circuit_and_stops_all_requests(response):
    rec = _Recorder(lambda req, proxy: response)
    client = _client(rec)

    assert await client.get_user_data("0xBlest_") is None
    assert client.is_blocked() is True
    assert client.last_outcome == "unavailable" and client.is_unavailable() is True
    # one request, no hop to the second proxy to get around the block
    assert len(rec.calls) == 1
    # while the circuit is open nothing goes out, for any handle
    assert await client.get_user_data("someone_else") is None
    assert client.last_outcome == "unavailable"
    assert len(rec.calls) == 1


async def test_dead_proxy_retries_on_another_proxy():
    state = {"n": 0}

    def flaky(request, proxy):
        state["n"] += 1
        if state["n"] == 1:
            raise httpx.ConnectError("proxy down", request=request)
        return _happy(request, proxy)

    rec = _Recorder(flaky)
    data = await _client(rec).get_user_data("0xBlest_")
    assert data["score"] == 587.22144
    first_two = [c[0] for c in rec.calls[:2]]
    assert first_two[0] != first_two[1]  # a different proxy the second time
    assert sorsa.is_blocked() is False


async def test_all_proxies_timing_out_is_unavailable_not_missing():
    """Live e2e regression (2026-09-17): both proxies hit ReadTimeout and the
    miss was recorded as "no score". It must read as unavailable, uncached."""
    def timeout(request, proxy):
        raise httpx.ReadTimeout("slow", request=request)

    rec = _Recorder(timeout)
    client = _client(rec)
    assert await client.get_user_data("0xBlest_") is None
    assert client.last_outcome == "unavailable"
    assert client.is_unavailable() is True
    assert sorsa.is_blocked() is False       # a slow network isn't pushback
    await client.get_user_data("0xBlest_")
    assert len(rec.calls) == 4               # not cached: the next call tries again


async def test_followers_info_failure_keeps_the_score():
    def no_info(request, proxy):
        if request.url.path == "/v1/accounts/search":
            return httpx.Response(200, json=ACCOUNT)
        return httpx.Response(500)

    data = await _client(_Recorder(no_info)).get_user_data("0xBlest_")
    assert data["score"] == 587.22144
    assert data["smart_followers"] is None
    assert data["top_followers"] is None


async def test_no_proxy_pool_refuses_to_scrape():
    def explode(proxy):
        raise AssertionError("must not open a connection without a proxy")

    client = SorsaClient([], client_factory=explode, min_interval_s=0)
    assert await client.get_user_data("0xBlest_") is None
    assert client.last_outcome == "unavailable"


async def test_invalid_handle_is_never_requested():
    rec = _Recorder(_happy)
    client = _client(rec)
    assert await client.get_user_data("not valid!") is None
    assert client.last_outcome == "not_indexed"
    assert await client.get_user_data("../admin") is None
    assert await client.get_user_data("") is None
    assert rec.calls == []


async def test_requests_are_spaced_out():
    rec = _Recorder(lambda req, proxy: httpx.Response(400, json=NOT_INDEXED_API))
    client = _client(rec, interval=0.1)
    await asyncio.gather(*(client.get_user_data(h) for h in ("a_one", "b_two", "c_three")))
    starts = sorted(c[2] for c in rec.calls)
    assert len(starts) == 3
    assert starts[-1] - starts[0] >= 0.15  # two ~100ms gaps (lenient for timer jitter)


# ---------------------------------------------------------------------------
# SCORE_PROVIDER switch
# ---------------------------------------------------------------------------
def test_score_provider_defaults_to_sorsa(monkeypatch):
    from app.integrations.score_provider import get_score_client

    monkeypatch.setattr(settings, "score_provider", "sorsa")
    assert isinstance(get_score_client(), SorsaClient)


def test_score_provider_legacy_analytics(monkeypatch):
    from app.integrations.loudrr_analytics import LoudrrAnalyticsClient
    from app.integrations.score_provider import get_score_client

    monkeypatch.setattr(settings, "score_provider", " Loudrr ")
    assert isinstance(get_score_client(), LoudrrAnalyticsClient)
