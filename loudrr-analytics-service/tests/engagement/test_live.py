"""Spec — realtime fan-out (app/engagement/live.py) + real OHLC candles.

The load-bearing property is ECONOMIC: N viewers of one token must cost ONE feed poll, not N.
So these tests pin the hub's fan-out, its poller lifecycle (start on first viewer, stop on
last), and the backpressure rule that a stalled browser can never stall the poller.
"""
import asyncio

import pytest

from app.engagement.api import _to_candles
from app.engagement.live import CLIENT_QUEUE_MAX, Hub


# ── candles ──────────────────────────────────────────────────────────────────

def test_to_candles_keeps_full_ohlcv():
    """We fetch [ts,o,h,l,c,v] and used to keep close alone — a real candle chart needs all
    of it, and `price` must stay as an alias for close so line callers don't break."""
    bars = _to_candles([[1700000000, "1.0", "1.5", "0.9", "1.2", "5000"]])
    assert bars == [{"ts": 1700000000, "open": 1.0, "high": 1.5, "low": 0.9,
                     "close": 1.2, "volume": 5000.0, "price": 1.2}]


def test_to_candles_drops_corrupt_and_short_rows():
    """A zero/negative bar is corrupt feed data; charted, it drags the whole price domain
    to zero and blows up a log scale."""
    assert _to_candles([
        [1700000000, 0, 0, 0, 0, 0],          # all-zero
        [1700000300, 1.0, 1.5, -0.2, 1.2, 1],  # negative low
        [1700000600, 1.0, 1.5],                # truncated row
        [1700000900, 1.0, 1.5, 0.9, None, 1],  # null close
    ]) == []


def test_to_candles_dedupes_and_sorts():
    """The feed repeats the newest (still-forming) bar across overlapping pulls, and
    lightweight-charts THROWS on duplicate/unordered times. Later row wins — it's the
    fresher read of the same bar."""
    bars = _to_candles([
        [1700000300, 2.0, 2.0, 2.0, 2.0, 1],
        [1700000000, 1.0, 1.0, 1.0, 1.0, 1],
        [1700000300, 2.0, 3.0, 2.0, 2.9, 9],   # same ts, fresher
    ])
    assert [b["ts"] for b in bars] == [1700000000, 1700000300]
    assert bars[-1]["close"] == 2.9 and bars[-1]["volume"] == 9.0


def test_to_candles_tolerates_missing_volume():
    """Some pools return 5-element rows with no volume column — that's a real bar, not junk."""
    bars = _to_candles([[1700000000, 1.0, 1.5, 0.9, 1.2]])
    assert len(bars) == 1 and bars[0]["volume"] == 0.0


# ── hub fan-out ──────────────────────────────────────────────────────────────

@pytest.fixture
def hub(monkeypatch):
    h = Hub()
    # never start a real poller: these tests are about fan-out, not the feed
    monkeypatch.setattr(Hub, "_run", lambda self, topic: asyncio.sleep(3600))
    return h


async def test_broadcast_reaches_every_subscriber(hub):
    """The whole point: one poll, many viewers."""
    a = await hub.subscribe("CA1")
    b = await hub.subscribe("CA1")
    await hub.broadcast("CA1", {"type": "candle"})
    assert a.get_nowait() == {"type": "candle"}
    assert b.get_nowait() == {"type": "candle"}


async def test_broadcast_is_isolated_per_token(hub):
    a = await hub.subscribe("CA1")
    b = await hub.subscribe("CA2")
    await hub.broadcast("CA1", {"type": "trades"})
    assert a.qsize() == 1
    assert b.qsize() == 0


async def test_slow_client_drops_its_own_oldest_and_never_blocks(hub):
    """A stalled browser must not stall the poller or the other viewers — that failure mode
    would make a shared hub strictly worse than per-client polling."""
    slow = await hub.subscribe("CA1")
    fast = await hub.subscribe("CA1")
    for i in range(CLIENT_QUEUE_MAX + 10):
        await asyncio.wait_for(hub.broadcast("CA1", {"n": i}), timeout=1.0)

    assert slow.qsize() == CLIENT_QUEUE_MAX      # bounded, not grown
    assert slow.get_nowait()["n"] != 0           # oldest shed, newest kept
    drained = []
    while not fast.empty():
        drained.append(fast.get_nowait()["n"])
    assert drained[-1] == CLIENT_QUEUE_MAX + 9   # freshest frame always survives


async def test_broadcast_to_unwatched_topic_is_a_noop(hub):
    await hub.broadcast("nobody-watching", {"type": "candle"})  # must not raise


# ── poller lifecycle (the cost control) ──────────────────────────────────────

async def test_poller_starts_on_first_viewer_and_stops_on_last(hub):
    """An unwatched token must burn nothing."""
    assert hub.live_topics == []
    a = await hub.subscribe("CA1")
    b = await hub.subscribe("CA1")
    assert hub._tasks["CA1"] is not None
    task = hub._tasks["CA1"]

    await hub.unsubscribe("CA1", a)
    assert "CA1" in hub._tasks and not task.cancelled()   # b is still watching

    await hub.unsubscribe("CA1", b)
    assert hub.live_topics == [] and "CA1" not in hub._tasks
    await asyncio.sleep(0)
    assert task.cancelled() or task.done()


async def test_unsubscribe_is_idempotent(hub):
    """A socket can die and be torn down twice; that must not explode."""
    q = await hub.subscribe("CA1")
    await hub.unsubscribe("CA1", q)
    await hub.unsubscribe("CA1", q)


async def test_live_topics_are_bounded(hub, monkeypatch):
    """Past the cap, late subscribers still get broadcasts — a bound, not a failure."""
    monkeypatch.setattr("app.engagement.live.MAX_LIVE_TOPICS", 2)
    for i in range(5):
        await hub.subscribe(f"CA{i}")
    assert len(hub._tasks) == 2
    assert len(hub.live_topics) == 5          # all still subscribed
    q = await hub.subscribe("CA9")
    await hub.broadcast("CA9", {"type": "call"})
    assert q.qsize() == 1


# ── timeframe routing ────────────────────────────────────────────────────────

async def test_hub_tracks_watched_timeframes(hub):
    """A candle only makes sense for ONE granularity: streaming a 5m bar to a viewer on the
    4h chip would silently corrupt their series, so the poller must know which are watched."""
    a = await hub.subscribe("CA1", "5m")
    b = await hub.subscribe("CA1", "4h")
    assert hub.timeframes("CA1") == {"5m", "4h"}

    await hub.unsubscribe("CA1", b)
    assert hub.timeframes("CA1") == {"5m"}     # nobody on 4h -> stop fetching it
    await hub.unsubscribe("CA1", a)
    assert hub.timeframes("CA1") == set()


async def test_shared_timeframe_is_fetched_once(hub):
    """The common case — everyone on the default chip — must cost one candle pull."""
    for _ in range(5):
        await hub.subscribe("CA1", "5m")
    assert hub.timeframes("CA1") == {"5m"}
    assert hub.subscriber_count("CA1") == 5


# ── the WS route itself ──────────────────────────────────────────────────────

def test_websocket_streams_frames_and_cleans_up(monkeypatch):
    """End-to-end over a real socket: connect -> hello -> broadcast lands -> disconnect
    releases the subscription (a leak here would keep polling a token nobody is watching)."""
    from fastapi.testclient import TestClient

    from app.engagement.live import hub as real_hub
    monkeypatch.setattr(Hub, "_run", lambda self, topic: asyncio.sleep(3600))
    from app.main import app

    with TestClient(app) as tc:
        with tc.websocket_connect("/v1/kol-calls/live?contract=CA1&timeframe=4h") as ws:
            hello = ws.receive_json()
            assert hello == {"type": "hello", "contract": "CA1", "timeframe": "4h",
                             "pollSeconds": pytest.approx(5.0)}
            assert real_hub.timeframes("CA1") == {"4h"}
            # broadcast from the portal's own loop — the app runs in TestClient's thread
            ws.portal.call(real_hub.broadcast, "CA1", {"type": "call", "contract": "CA1"})
            assert ws.receive_json() == {"type": "call", "contract": "CA1"}
    assert real_hub.subscriber_count("CA1") == 0
    assert "CA1" not in real_hub.live_topics


def test_websocket_rejects_a_missing_contract(monkeypatch):
    """No token = nothing to stream; close rather than hold an idle socket open."""
    from fastapi.testclient import TestClient

    from starlette.websockets import WebSocketDisconnect
    monkeypatch.setattr(Hub, "_run", lambda self, topic: asyncio.sleep(3600))
    from app.main import app

    with TestClient(app) as tc:
        with pytest.raises(WebSocketDisconnect):
            with tc.websocket_connect("/v1/kol-calls/live?contract=") as ws:
                ws.receive_json()


def test_websocket_falls_back_on_an_unknown_timeframe(monkeypatch):
    """An unknown chip would make the poller fetch a granularity no client can use."""
    from fastapi.testclient import TestClient

    monkeypatch.setattr(Hub, "_run", lambda self, topic: asyncio.sleep(3600))
    from app.main import app

    with TestClient(app) as tc:
        with tc.websocket_connect("/v1/kol-calls/live?contract=CA2&timeframe=bogus") as ws:
            assert ws.receive_json()["timeframe"] == "5m"
