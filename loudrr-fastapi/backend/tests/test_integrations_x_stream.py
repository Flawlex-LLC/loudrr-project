"""XStreamClient (integrations/x_stream.py) against httpx.MockTransport — the
request each method sends, and how every gateway answer is read."""
import json

import httpx
import pytest

from app.core.config import settings
from app.integrations.x_stream import GatewayUnavailable, XStreamClient, get_x_stream_client

BASE = "https://gateway.test"


def make_client(handler, *, api_key="test-key", base_url=BASE):
    """A client whose requests go to `handler` (and get recorded on .requests)."""
    requests: list[httpx.Request] = []

    def _record(request: httpx.Request):
        requests.append(request)
        return handler(request)

    client = XStreamClient(api_key=api_key, base_url=base_url, transport=httpx.MockTransport(_record))
    client.requests = requests
    return client


def reply(payload, status=200):
    return lambda request: httpx.Response(status, json=payload)


def body(request: httpx.Request) -> dict:
    return json.loads(request.content)


# ---- config ----
def test_websocket_url_and_headers():
    assert XStreamClient(api_key="k", base_url="https://gw.example.com/").websocket_url() == \
        "wss://gw.example.com/twitter/tweet/websocket"
    assert XStreamClient(api_key="k", base_url="http://localhost:8080").websocket_url() == \
        "ws://localhost:8080/twitter/tweet/websocket"
    assert XStreamClient(api_key="k", base_url="wss://already").websocket_url() == \
        "wss://already/twitter/tweet/websocket"
    assert XStreamClient(api_key="secret", base_url=BASE).headers() == {"x-api-key": "secret"}


def test_configured_and_default_settings(monkeypatch):
    monkeypatch.setattr(settings, "loudrr_gateway_api", "from-settings")
    monkeypatch.setattr(settings, "gateway_base_url", "https://gw.settings.test/")
    client = get_x_stream_client()
    assert client.configured is True
    assert client.api_key == "from-settings"
    assert client.base_url == "https://gw.settings.test"

    monkeypatch.setattr(settings, "loudrr_gateway_api", "")
    assert get_x_stream_client().configured is False
    # an explicit empty key is not replaced by the settings one
    monkeypatch.setattr(settings, "loudrr_gateway_api", "from-settings")
    assert XStreamClient(api_key="").configured is False


async def test_unconfigured_client_never_calls_out():
    def _handler(request):
        raise AssertionError("must not send a request without a key")

    client = make_client(_handler, api_key="")
    with pytest.raises(GatewayUnavailable):
        await client.user_info("acme")
    with pytest.raises(GatewayUnavailable):
        await client.list_monitors()


# ---- user_info ----
async def test_user_info_found():
    user = {"id": "44196397", "userName": "elonmusk", "name": "Elon", "profilePicture": "https://p"}
    client = make_client(reply({"status": "success", "msg": "success", "data": user}))
    assert await client.user_info("elonmusk") == user
    [req] = client.requests
    assert req.method == "GET"
    assert req.url.path == "/twitter/user/info"
    assert req.url.params["userName"] == "elonmusk"
    assert req.headers["x-api-key"] == "test-key"


@pytest.mark.parametrize("payload", [
    {"status": "error", "msg": "User not found"},
    {"status": "error", "msg": "user is suspended"},
    {"status": "success", "data": {}},
    {"status": "success", "data": None},
    {},
])
async def test_user_info_missing_user_is_none(payload):
    client = make_client(reply(payload))
    assert await client.user_info("ghost") is None


@pytest.mark.parametrize("payload", [
    {"status": "error", "msg": "Insufficient credits"},
    {"status": "error"},
])
async def test_user_info_other_errors_raise(payload):
    client = make_client(reply(payload))
    with pytest.raises(GatewayUnavailable):
        await client.user_info("acme")


@pytest.mark.parametrize("status", [500, 502, 401, 402, 429])
async def test_http_error_status_raises(status):
    client = make_client(reply({"status": "error", "msg": "nope"}, status=status))
    with pytest.raises(GatewayUnavailable) as exc:
        await client.user_info("acme")
    assert str(status) in str(exc.value)


async def test_network_error_raises():
    def _handler(request):
        raise httpx.ConnectError("connection refused", request=request)

    client = make_client(_handler)
    with pytest.raises(GatewayUnavailable):
        await client.search_latest("from:acme")


async def test_timeout_raises():
    def _handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    client = make_client(_handler)
    with pytest.raises(GatewayUnavailable):
        await client.add_monitor("acme")


async def test_non_json_response_raises():
    client = make_client(lambda request: httpx.Response(200, text="<html>bad gateway</html>"))
    with pytest.raises(GatewayUnavailable):
        await client.list_monitors()


async def test_non_dict_json_is_treated_as_empty():
    client = make_client(reply([1, 2, 3]))
    assert await client.list_monitors() == []
    assert await client.user_info("acme") is None


# ---- search_latest ----
async def test_search_latest_parses_page():
    tweets = [{"id": "1", "text": "a"}, {"id": "2", "text": "b"}]
    client = make_client(reply({"tweets": [*tweets, "junk"], "has_next_page": True, "next_cursor": "CUR"}))
    got = await client.search_latest("(from:acme) since_time:1")
    assert got == (tweets, "CUR", True)
    [req] = client.requests
    assert req.url.path == "/twitter/tweet/advanced_search"
    assert req.url.params["query"] == "(from:acme) since_time:1"
    assert req.url.params["queryType"] == "Latest"
    assert "cursor" not in req.url.params


async def test_search_latest_with_cursor_and_empty_page():
    client = make_client(reply({"tweets": [], "has_next_page": False, "next_cursor": None}))
    assert await client.search_latest("q", "CUR") == ([], "", False)
    assert client.requests[0].url.params["cursor"] == "CUR"

    client = make_client(reply({}))
    assert await client.search_latest("q") == ([], "", False)


async def test_search_latest_error_status_raises():
    client = make_client(reply({"status": "error", "msg": "rate limited"}))
    with pytest.raises(GatewayUnavailable):
        await client.search_latest("q")


# ---- monitor list ----
async def test_list_monitors():
    rows = [{"id_for_user": "a1", "x_user_screen_name": "acme"}]
    client = make_client(reply({"status": "success", "data": [*rows, None, "x"]}))
    assert await client.list_monitors() == rows
    assert client.requests[0].url.path == "/oapi/x_user_stream/get_user_to_monitor_tweet"

    assert await make_client(reply({"status": "success", "data": None})).list_monitors() == []


async def test_add_monitor_success():
    client = make_client(reply({"status": "success", "msg": "ok"}))
    await client.add_monitor("acme")
    [req] = client.requests
    assert req.method == "POST"
    assert req.url.path == "/oapi/x_user_stream/add_user_to_monitor_tweet"
    assert body(req) == {"x_user_name": "acme"}


@pytest.mark.parametrize("msg", ["User already monitored", "user exists in monitor list", "ALREADY ADDED"])
async def test_add_monitor_already_monitored_is_fine(msg):
    client = make_client(reply({"status": "error", "msg": msg}))
    await client.add_monitor("acme")  # no raise


@pytest.mark.parametrize("payload", [{"status": "error", "msg": "monitor limit reached"}, {}])
async def test_add_monitor_failure_raises(payload):
    client = make_client(reply(payload))
    with pytest.raises(GatewayUnavailable):
        await client.add_monitor("acme")


async def test_remove_monitor_removes_every_matching_row():
    rows = [
        {"id_for_user": "a1", "x_user_screen_name": "Acme"},
        {"id_for_user": "b1", "x_user_screen_name": "other"},
        {"id_for_user": "a2", "x_user_name": "acme"},
    ]

    def _handler(request):
        if request.url.path.endswith("get_user_to_monitor_tweet"):
            return httpx.Response(200, json={"status": "success", "data": rows})
        assert request.url.path == "/oapi/x_user_stream/remove_user_to_monitor_tweet"
        return httpx.Response(200, json={"status": "success"})

    client = make_client(_handler)
    assert await client.remove_monitor("ACME") is True
    removed = [body(r) for r in client.requests if r.method == "POST"]
    assert removed == [{"id_for_user": "a1"}, {"id_for_user": "a2"}]


async def test_remove_monitor_none_matching():
    client = make_client(reply({"status": "success", "data": [{"id_for_user": "b1", "x_user_screen_name": "other"}]}))
    assert await client.remove_monitor("acme") is False
    assert [r.method for r in client.requests] == ["GET"]


async def test_remove_monitor_failure_raises():
    def _handler(request):
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id_for_user": "a1", "x_user_screen_name": "acme"}]})
        return httpx.Response(200, json={"status": "error", "msg": "not allowed"})

    with pytest.raises(GatewayUnavailable):
        await make_client(_handler).remove_monitor("acme")


@pytest.mark.parametrize("msg", ["user does not exist", "User doesn't exist", "not exist"])
async def test_add_monitor_missing_user_is_a_failure(msg):
    client = make_client(reply({"status": "error", "msg": msg}))
    with pytest.raises(GatewayUnavailable):
        await client.add_monitor("ghost")


async def test_users_by_ids_batches_of_100():
    seen = []

    def handler(request):
        ids = request.url.params["userIds"].split(",")
        seen.append((request.url.path, len(ids)))
        return httpx.Response(200, json={"status": "success", "msg": "", "users": [
            {"id": i, "userName": f"u{i}"} for i in ids[:2]
        ] + ["junk"]})

    client = make_client(handler)
    users = await client.users_by_ids([str(n) for n in range(150)] + ["not-a-number"])
    assert seen == [("/twitter/user/batch_info_by_ids", 100), ("/twitter/user/batch_info_by_ids", 50)]
    assert [u["userName"] for u in users] == ["u0", "u1", "u100", "u101"]


async def test_users_by_ids_empty_makes_no_call():
    client = make_client(reply({}))
    assert await client.users_by_ids([]) == []
    assert client.requests == []


async def test_users_by_ids_error_raises():
    client = make_client(reply({"status": "error", "msg": "upstream down"}))
    with pytest.raises(GatewayUnavailable):
        await client.users_by_ids(["1"])
