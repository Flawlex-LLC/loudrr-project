"""Realtime fan-out for KOL calls, rides and candles (WS /v1/kol-calls/live?contract=X).

THE ECONOMIC TRICK, and the whole reason this module exists: we poll the free keyless
GeckoTerminal feed **once per token**, not once per viewer, and fan the result out over our
own WebSocket. A thousand people watching $PENGU costs exactly what one person costs, so
"realtime like an exchange" stays free at any audience size. A per-viewer poll would multiply
the free feed's rate cap by the audience and die on the first busy token.

Lifecycle: the poller for a token STARTS on its first subscriber and STOPS on its last one —
an unwatched token burns nothing. Nothing here is on the request path of the score funnel.

Backpressure: each client has a bounded queue and a slow/dead socket drops its own oldest
frames. A stalled browser can NEVER stall the poller or the other viewers — the one failure
mode that would make a shared hub worse than per-client polling.

Design contract (inherited from api.py): this can never 500 and never raises into the app.
Feed down -> no frames, socket stays open, UI keeps its last paint.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

logger = logging.getLogger("eng.live")

router = APIRouter()

# Poll cadence per WATCHED token. The feed's free cap is 30/min from one IP, but
# _ProxiedReader lifts its self-cap to 240/min when ONCHAIN_PROXIES is set (Webshare
# rotation), which is what makes several concurrent live tokens affordable. Each beat costs
# 2 requests (trades + ohlcv), so 5s => 24 req/min per watched token.
POLL_S = 5.0
# Hard ceiling on concurrently-polled tokens. Past this, late subscribers still receive the
# hub's broadcasts (they just don't get their own poller) — a bound, not a failure.
MAX_LIVE_TOPICS = 12
# Bounded per-client buffer: a slow socket drops its oldest frames instead of stalling the hub.
CLIENT_QUEUE_MAX = 64


class Hub:
    """Topic -> subscriber fan-out with a per-topic background poller.

    A "topic" is a token contract; each subscriber also declares the chart timeframe it is
    watching. Trades are timeframe-independent (one pull serves everyone), but a candle only
    makes sense for ONE granularity — streaming a 5m bar to a viewer on the 4h chip would
    corrupt their series — so the poller fetches the *set* of timeframes currently being
    watched (normally exactly one) and tags each frame with its own.
    """

    def __init__(self) -> None:
        # topic -> {queue: timeframe}. A dict (not a set) so a subscriber leaving also
        # retires the timeframe nobody is watching any more.
        self._topics: dict[str, dict[asyncio.Queue, str]] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()

    def subscriber_count(self, topic: str) -> int:
        return len(self._topics.get(topic, ()))

    def timeframes(self, topic: str) -> set[str]:
        return set(self._topics.get(topic, {}).values())

    @property
    def live_topics(self) -> list[str]:
        return sorted(self._topics)

    async def subscribe(self, topic: str, timeframe: str = "5m") -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=CLIENT_QUEUE_MAX)
        async with self._lock:
            self._topics.setdefault(topic, {})[q] = timeframe
            # first viewer of this token -> start its poller (bounded by MAX_LIVE_TOPICS)
            if topic not in self._tasks and len(self._tasks) < MAX_LIVE_TOPICS:
                self._tasks[topic] = asyncio.create_task(self._run(topic))
        return q

    async def unsubscribe(self, topic: str, q: asyncio.Queue) -> None:
        async with self._lock:
            subs = self._topics.get(topic)
            if subs is not None:
                subs.pop(q, None)
                if not subs:
                    # last viewer left -> stop polling this token, spend nothing
                    self._topics.pop(topic, None)
                    task = self._tasks.pop(topic, None)
                    if task is not None:
                        task.cancel()

    async def broadcast(self, topic: str, msg: dict[str, Any]) -> None:
        """Push a frame to every subscriber. Never blocks on a slow client."""
        for q in list(self._topics.get(topic, ())):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                # slow consumer: drop ITS oldest frame and retry once. Realtime data is
                # only useful fresh, so shedding the stale frame beats stalling everyone.
                with contextlib.suppress(asyncio.QueueEmpty):
                    q.get_nowait()
                with contextlib.suppress(asyncio.QueueFull):
                    q.put_nowait(msg)

    async def _run(self, topic: str) -> None:
        """Poll one token until nobody is watching it. Errors never escape."""
        # Lazy import: api.py owns the feed snapshots and imports nothing from live.py,
        # so importing it here (not at module load) keeps the dependency one-directional.
        from app.engagement.api import candle_snapshot, tape_snapshot

        # dict as an insertion-ordered dedup — eviction must keep the MOST-RECENT keys, and
        # list(set)[-N:] would slice hash order and retain arbitrary ones.
        seen: dict[str, None] = {}   # trade keys already sent, so we only push NEW trades
        first = True
        while True:
            try:
                await asyncio.sleep(POLL_S)
                trades = await tape_snapshot(topic, fresh=True)
                fresh = []
                for t in trades:
                    key = f"{t.get('ts')}|{t.get('wallet')}|{t.get('volumeUsd')}"
                    if key in seen:
                        continue
                    seen[key] = None
                    fresh.append(t)
                if len(seen) > 4000:      # bound the dedup on busy pools, keeping recent keys
                    seen = dict.fromkeys(list(seen)[-2000:])
                # First beat only primes `seen` — replaying the pool's whole backlog as
                # "live" would fake a burst of activity the moment anyone opens the page.
                if fresh and not first:
                    await self.broadcast(topic, {"type": "trades", "contract": topic,
                                                 "trades": fresh[:20]})
                # one candle pull per WATCHED timeframe (usually exactly one); each frame
                # carries its timeframe so a client on another chip ignores it
                for tf in (self.timeframes(topic) or {"5m"}):
                    candle = await candle_snapshot(topic, tf)
                    if candle is not None:
                        await self.broadcast(topic, {"type": "candle", "contract": topic,
                                                     "timeframe": tf, "candle": candle})
                first = False
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — a dead feed must not kill the socket
                logger.warning("live poll failed for %s", topic, exc_info=True)


hub = Hub()


@router.websocket("/kol-calls/live")
async def kol_calls_live(ws: WebSocket, contract: str = Query(""),
                         timeframe: str = Query("5m")) -> None:
    """Realtime frames for one token: {type: trades|candle|call|hello}.

    - trades: new pool trades (KOL buys flagged) since the last frame
    - candle: the in-progress candle for `timeframe`, for series.update() without a refetch
    - call:   a tracked KOL just posted about this token (pushed by the X webhook)
    """
    await ws.accept()
    topic = (contract or "").strip()
    if not topic:
        await ws.close(code=1008)
        return
    # validate against the chart's own chip set — an unknown timeframe would make the
    # poller fetch a granularity no client can use
    from app.engagement.api import CHART_TFS
    tf = timeframe if timeframe in CHART_TFS else "5m"

    q = await hub.subscribe(topic, tf)
    pump: asyncio.Task | None = None
    try:
        # hello send lives INSIDE the try: if it raises (client already gone), the finally
        # must still run hub.unsubscribe — otherwise the subscription (and, for a first
        # subscriber, its poller) leaks on a token nobody is watching.
        await ws.send_text(json.dumps({"type": "hello", "contract": topic,
                                       "timeframe": tf, "pollSeconds": POLL_S}))

        async def _pump() -> None:
            while True:
                msg = await q.get()
                await ws.send_text(json.dumps(msg))

        pump = asyncio.create_task(_pump())
        # Drain client frames so close/ping is noticed promptly; the client sends nothing
        # meaningful, this is purely how we learn the socket died.
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001 — a broken socket is normal, not an error
        logger.debug("live socket closed for %s", topic, exc_info=True)
    finally:
        # unsubscribe MUST run even if awaiting the cancelled pump re-raises a non-Cancelled
        # exception (e.g. the pump died on a ConnectionClosed send) — a nested finally
        # guarantees the subscription is always released, never leaking a poller.
        try:
            if pump is not None:
                pump.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await pump
        finally:
            await hub.unsubscribe(topic, q)


async def push_call(contract: str, call: dict[str, Any]) -> None:
    """Broadcast a fresh KOL call (used by the X webhook). Safe to call from anywhere."""
    with contextlib.suppress(Exception):
        await hub.broadcast(contract, {"type": "call", "contract": contract, "call": call})


async def push_ride(contract: str, ride: dict[str, Any]) -> None:
    """Broadcast a fresh KOL wallet trade (used by the wallet watcher). Safe from anywhere."""
    with contextlib.suppress(Exception):
        await hub.broadcast(contract, {"type": "ride", "contract": contract, "ride": ride})
