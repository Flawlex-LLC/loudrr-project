"""Tests for app.integrations.telegram — the Telegram Bot API client used by
the outbox drain to deliver waitlist/approval cards.

The module exposes one public coroutine on ``TelegramClient`` (``send_message``)
and a factory ``get_telegram_client``. Behavior contract (from the module's
docstring + source):

* If ``TELEGRAM_BOT_TOKEN`` is unset, ``send_message`` returns ``False`` and
  performs no HTTP request (warning is logged, no exception).
* Otherwise it POSTs to ``https://api.telegram.org/bot<token>/sendMessage``
  with ``{"chat_id", "text", "parse_mode"}`` JSON.
* On 4xx/5xx it calls ``resp.raise_for_status()`` so the outbox sees the
  ``HTTPStatusError`` and marks the event for retry. (The module does NOT
  swallow these — that's intentional.)
* On network errors (``httpx.ConnectError`` etc.) the exception bubbles up
  for the same reason.
* On 200 OK it returns ``True``.

There is no built-in retry/backoff in this module and no HTML-escaping —
those concerns live elsewhere (the outbox handles retries; callers format
their own text). We therefore don't have a retry-on-429 test or an escaping
test; instead we verify that 429 propagates as ``HTTPStatusError`` (which is
what tells the outbox to back off).

All HTTP is intercepted with ``httpx.MockTransport`` — no real network.
"""
from __future__ import annotations

import httpx
import pytest

from app.integrations import telegram as telegram_mod
from app.integrations.telegram import TelegramClient, get_telegram_client


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _install_mock_transport(monkeypatch, handler):
    """Monkeypatch httpx.AsyncClient inside the telegram module so any
    ``async with httpx.AsyncClient(...)`` uses a MockTransport that runs
    ``handler(request) -> httpx.Response``. Returns the list of captured
    requests for inspection."""
    captured: list[httpx.Request] = []

    def _wrapped(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return handler(request)

    transport = httpx.MockTransport(_wrapped)
    real_cls = httpx.AsyncClient

    def _factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_cls(*args, **kwargs)

    monkeypatch.setattr(telegram_mod.httpx, "AsyncClient", _factory)
    return captured


# ---------------------------------------------------------------------------
# Construction / factory
# ---------------------------------------------------------------------------

def test_constructor_uses_explicit_token_over_settings():
    """Explicit ``bot_token`` arg always wins over ``settings.telegram_bot_token``."""
    c = TelegramClient(bot_token="explicit-123")
    assert c.bot_token == "explicit-123"


def test_constructor_falls_back_to_settings(monkeypatch):
    """When no token is passed, the client picks up ``settings.telegram_bot_token``."""
    monkeypatch.setattr(telegram_mod.settings, "telegram_bot_token", "from-settings-xyz")
    c = TelegramClient()
    assert c.bot_token == "from-settings-xyz"


def test_get_telegram_client_returns_telegramclient_instance():
    """The factory wires up a default-config client (used by outbox)."""
    c = get_telegram_client()
    assert isinstance(c, TelegramClient)


# ---------------------------------------------------------------------------
# send_message — missing-token short-circuit
# ---------------------------------------------------------------------------

async def test_send_message_returns_false_when_token_empty(monkeypatch):
    """Empty string token → log warning, return False, do NOT hit the network."""
    called = {"hit": False}

    def _handler(_request):
        called["hit"] = True
        return httpx.Response(200, json={"ok": True})

    _install_mock_transport(monkeypatch, _handler)
    c = TelegramClient(bot_token="")
    result = await c.send_message(chat_id=42, text="hello")
    assert result is False
    assert called["hit"] is False, "must not POST when token is missing"


async def test_send_message_logs_warning_when_token_missing(monkeypatch, caplog):
    """The missing-token path emits a warning so ops notices in dev/staging."""
    import logging

    _install_mock_transport(monkeypatch, lambda _r: httpx.Response(200, json={"ok": True}))
    c = TelegramClient(bot_token="")
    with caplog.at_level(logging.WARNING, logger="app.integrations.telegram"):
        await c.send_message(chat_id=1, text="x")
    assert any("TELEGRAM_BOT_TOKEN" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# send_message — success path
# ---------------------------------------------------------------------------

async def test_send_message_success_returns_true(monkeypatch):
    """200 OK → coroutine resolves to ``True``."""
    _install_mock_transport(
        monkeypatch,
        lambda _r: httpx.Response(200, json={"ok": True, "result": {"message_id": 1}}),
    )
    c = TelegramClient(bot_token="test-token")
    assert await c.send_message(chat_id=42, text="hello") is True


async def test_send_message_posts_to_correct_url(monkeypatch):
    """URL is ``https://api.telegram.org/bot<token>/sendMessage``."""
    captured = _install_mock_transport(monkeypatch, lambda _r: httpx.Response(200, json={"ok": True}))
    c = TelegramClient(bot_token="abc-token")
    await c.send_message(chat_id=42, text="hi")
    assert len(captured) == 1
    assert str(captured[0].url) == "https://api.telegram.org/botabc-token/sendMessage"
    assert captured[0].method == "POST"


async def test_send_message_payload_contains_chat_id_text_parse_mode(monkeypatch):
    """Body is JSON with the three required fields; parse_mode defaults to HTML."""
    import json as _json

    captured = _install_mock_transport(monkeypatch, lambda _r: httpx.Response(200, json={"ok": True}))
    c = TelegramClient(bot_token="t")
    await c.send_message(chat_id=99, text="<b>hi</b>")

    body = _json.loads(captured[0].content)
    assert body == {"chat_id": 99, "text": "<b>hi</b>", "parse_mode": "HTML"}


async def test_send_message_respects_custom_parse_mode(monkeypatch):
    """Callers can override parse_mode (e.g. ``MarkdownV2``)."""
    import json as _json

    captured = _install_mock_transport(monkeypatch, lambda _r: httpx.Response(200, json={"ok": True}))
    c = TelegramClient(bot_token="t")
    await c.send_message(chat_id=1, text="*x*", parse_mode="MarkdownV2")
    body = _json.loads(captured[0].content)
    assert body["parse_mode"] == "MarkdownV2"


async def test_send_message_does_not_escape_text(monkeypatch):
    """The client passes ``text`` through verbatim — escaping is the caller's
    responsibility. (Outbox/services format messages; this is a thin transport
    layer.) We assert raw HTML/markdown is sent unchanged."""
    import json as _json

    captured = _install_mock_transport(monkeypatch, lambda _r: httpx.Response(200, json={"ok": True}))
    c = TelegramClient(bot_token="t")
    raw = "<script>alert(1)</script> & 'quotes' *md*"
    await c.send_message(chat_id=1, text=raw)
    body = _json.loads(captured[0].content)
    assert body["text"] == raw


# ---------------------------------------------------------------------------
# send_message — error paths (must raise so outbox retries)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("status", [400, 401, 403, 404, 429])
async def test_send_message_raises_on_4xx(monkeypatch, status):
    """4xx responses → ``HTTPStatusError`` so the outbox marks for retry.

    This includes 429 (rate-limited) — there is intentionally no in-client
    retry/backoff; the outbox drain is the single retry authority."""
    _install_mock_transport(
        monkeypatch,
        lambda _r: httpx.Response(status, json={"ok": False, "description": "boom"}),
    )
    c = TelegramClient(bot_token="t")
    with pytest.raises(httpx.HTTPStatusError):
        await c.send_message(chat_id=1, text="x")


@pytest.mark.parametrize("status", [500, 502, 503, 504])
async def test_send_message_raises_on_5xx(monkeypatch, status):
    """5xx responses → ``HTTPStatusError`` so the outbox marks for retry."""
    _install_mock_transport(monkeypatch, lambda _r: httpx.Response(status, text="server down"))
    c = TelegramClient(bot_token="t")
    with pytest.raises(httpx.HTTPStatusError):
        await c.send_message(chat_id=1, text="x")


async def test_send_message_raises_on_network_error(monkeypatch):
    """Transport-level failure (DNS, refused, reset) → propagates as
    ``httpx.ConnectError``-family exception. Outbox catches and retries."""

    def _boom(_request):
        raise httpx.ConnectError("DNS failure", request=_request)

    _install_mock_transport(monkeypatch, _boom)
    c = TelegramClient(bot_token="t")
    with pytest.raises(httpx.ConnectError):
        await c.send_message(chat_id=1, text="x")


async def test_send_message_raises_on_timeout(monkeypatch):
    """Timeouts also propagate (separate exception class from ConnectError)."""

    def _slow(_request):
        raise httpx.ReadTimeout("too slow", request=_request)

    _install_mock_transport(monkeypatch, _slow)
    c = TelegramClient(bot_token="t")
    with pytest.raises(httpx.ReadTimeout):
        await c.send_message(chat_id=1, text="x")
