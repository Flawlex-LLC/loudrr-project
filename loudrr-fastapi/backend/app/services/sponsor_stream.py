"""Sponsored-account listener — runs inside the arq worker (worker.startup).

Two loops feed services/sponsors.ingest_tweets, and either one alone is enough:

  * push — one websocket to the Loudrr gateway, which delivers new tweets of the
    accounts on our monitor list. Once the gateway confirms the connection
    ({"event_type": "connected"}) and every SYNC_INTERVAL_S after, the monitor
    list is re-synced with the sponsored_accounts table. Reconnects forever
    (1s → 60s backoff); a connection silent for RECV_TIMEOUT_S (not even the
    gateway's ~20s ping) is replaced. The gateway allows ONE connection per
    key and closes a second one before "connected", so a duplicate process
    never touches the monitor list.
  * poll — every SPONSOR_POLL_SECONDS, an X search for the sponsors' original
    posts since the last successful poll (minus POLL_OVERLAP for search-index
    lag), plus a wider sweep every SWEEP_EVERY_S for posts indexed late.
    Empty searches are free. The last successful poll is kept in Redis so a
    restart resumes where it stopped. Hourly it also looks sponsors up by
    their permanent X id to follow @handle renames.

A tweet delivered by both is imported once (unique index on sponsored tweet id).
"""
import asyncio
import json
import logging
import time
from datetime import datetime, timedelta

import websockets

from app.core.config import settings
from app.core.time_utils import utcnow
from app.integrations.x_stream import GatewayUnavailable, XStreamClient, get_x_stream_client
from app.services import sponsors

logger = logging.getLogger(__name__)

RECV_TIMEOUT_S = 120
MAX_BACKOFF_S = 60
SYNC_INTERVAL_S = 600
# every poll re-searches this much before the previous one: X's search index
# often lags a new post by 30-60s+ (live: a 30s overlap left posts waiting for
# the sweep, 143s; 90s kept every import under 35s). The overlap re-returns a
# tweet ~5 times at ~15 credits each — about $0.0006 extra per sponsored post.
POLL_OVERLAP = timedelta(seconds=90)
# ...and a periodic wider sweep catches posts indexed minutes late
SWEEP_EVERY_S = 300
SWEEP_WINDOW = timedelta(minutes=10)
HANDLE_REFRESH_S = 3600
MAX_POLL_BACKOFF_S = 300
# failures are logged on the first one and then every Nth, not every poll
LOG_EVERY_N_FAILURES = 30
WATERMARK_KEY = "sponsor_stream:last_poll"


async def handle_frame(raw, session_factory) -> int:
    """Ingest one websocket frame; returns the number of posts created."""
    try:
        frame = json.loads(raw)
    except (TypeError, ValueError):
        logger.warning("sponsor stream: non-JSON frame %r", str(raw)[:120])
        return 0
    tweets = sponsors.tweets_in_frame(frame)
    if not tweets:
        return 0
    async with session_factory() as db:
        return len(await sponsors.ingest_tweets(db, tweets))


def _is_connected_frame(raw) -> bool:
    if not isinstance(raw, (str, bytes)) or len(raw) > 1000:
        return False
    try:
        frame = json.loads(raw)
    except (TypeError, ValueError):
        return False
    return isinstance(frame, dict) and frame.get("event_type") == "connected"


async def sync_monitors(session_factory, client: XStreamClient) -> None:
    async with session_factory() as db:
        try:
            await sponsors.sync_monitors(db, client)
        except GatewayUnavailable as e:
            logger.warning("sponsor stream: monitor sync failed: %s", e)
        except Exception:
            # runs as a fire-and-forget task: an unlogged error would vanish
            logger.exception("sponsor stream: monitor sync failed")


def _stopped(stop: asyncio.Event | None) -> bool:
    return stop is not None and stop.is_set()


async def _pause(seconds: float, stop: asyncio.Event | None, sleep) -> None:
    if stop is None:
        await sleep(seconds)
        return
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
    except asyncio.TimeoutError:
        pass


async def push_loop(session_factory, client: XStreamClient, *, connect=None,
                    stop: asyncio.Event | None = None, sleep=asyncio.sleep,
                    clock=time.monotonic) -> None:
    connect = connect or websockets.connect
    backoff = 1
    background: set[asyncio.Task] = set()

    def start_sync():
        task = asyncio.create_task(sync_monitors(session_factory, client))
        background.add(task)
        task.add_done_callback(background.discard)

    try:
        while not _stopped(stop):
            connected_at = None
            try:
                async with connect(
                    client.websocket_url(), additional_headers=client.headers(),
                    open_timeout=20, max_size=10 * 1024 * 1024,
                ) as ws:
                    connected_at = clock()
                    last_sync = None
                    while not _stopped(stop):
                        raw = await asyncio.wait_for(ws.recv(), timeout=RECV_TIMEOUT_S)
                        if last_sync is None and _is_connected_frame(raw):
                            logger.info("sponsor stream connected")
                            last_sync = clock()
                            start_sync()
                        elif last_sync is not None and clock() - last_sync >= SYNC_INTERVAL_S:
                            last_sync = clock()
                            start_sync()
                        try:
                            await handle_frame(raw, session_factory)
                        except Exception:
                            # a bad frame / DB hiccup must not drop the connection
                            logger.exception("sponsor stream: frame ingest failed")
            except asyncio.CancelledError:
                raise
            except Exception as e:  # handshake, network, close, recv timeout
                if backoff == 1 or backoff == MAX_BACKOFF_S:
                    logger.warning("sponsor stream disconnected: %r", e)
            if _stopped(stop):
                break
            if connected_at is not None and clock() - connected_at > 60:
                backoff = 1
            await _pause(backoff, stop, sleep)
            backoff = min(backoff * 2, MAX_BACKOFF_S)
    finally:
        for task in background:
            task.cancel()


async def _load_watermark(redis) -> datetime | None:
    if redis is None:
        return None
    try:
        raw = await redis.get(WATERMARK_KEY)
        if raw is None:
            return None
        return datetime.fromisoformat(raw.decode() if isinstance(raw, bytes) else str(raw))
    except Exception as e:
        logger.warning("sponsor poll: couldn't read the last poll time: %r", e)
        return None


async def _save_watermark(redis, started: datetime) -> None:
    if redis is None:
        return
    try:
        await redis.set(WATERMARK_KEY, started.isoformat(), ex=int(sponsors.MAX_TWEET_AGE.total_seconds()))
    except Exception as e:
        logger.warning("sponsor poll: couldn't save the last poll time: %r", e)


async def poll_loop(session_factory, client: XStreamClient, *, interval_s: float,
                    stop: asyncio.Event | None = None, sleep=asyncio.sleep,
                    redis=None, clock=time.monotonic) -> None:
    since: datetime | None = None
    failures = 0
    last_sweep = clock()
    last_refresh: float | None = None
    while not _stopped(stop):
        started = utcnow()
        try:
            async with session_factory() as db:
                if since is None:
                    # resume from the last successful poll (any process), else
                    # from where each sponsor could last have missed a post
                    mark = await _load_watermark(redis)
                    since = (mark if mark is not None else await sponsors.poll_since(db)) - POLL_OVERLAP
                window = since
                sweeping = clock() - last_sweep >= SWEEP_EVERY_S
                if sweeping:
                    window = min(window, started - SWEEP_WINDOW)
                window = max(window, utcnow() - sponsors.MAX_TWEET_AGE)
                if last_refresh is None or clock() - last_refresh >= HANDLE_REFRESH_S:
                    last_refresh = clock()
                    try:
                        renamed = await sponsors.refresh_handles(db, client)
                        if renamed:
                            logger.info("sponsor handles renamed: %s", renamed)
                    except GatewayUnavailable as e:
                        logger.warning("sponsor handle refresh failed: %s", e)
                created = await sponsors.poll_new_posts(db, client, since=window)
            if created:
                logger.info("sponsor poll: created %s posts", created)
            if sweeping:
                last_sweep = clock()
            since = started - POLL_OVERLAP
            await _save_watermark(redis, started)
            if failures:
                logger.info("sponsor poll recovered after %s failed polls", failures)
            failures = 0
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # keep the window: the next successful poll re-covers it
            failures += 1
            if failures == 1 or failures % LOG_EVERY_N_FAILURES == 0:
                if isinstance(e, GatewayUnavailable):
                    logger.warning("sponsor poll failed (%s in a row): %s", failures, e)
                else:
                    logger.exception("sponsor poll failed (%s in a row)", failures)
        delay = interval_s
        if failures:
            delay = min(interval_s * 2 ** min(failures, 10), max(interval_s, MAX_POLL_BACKOFF_S))
        await _pause(delay, stop, sleep)


async def run_forever(*, session_factory=None, client: XStreamClient | None = None,
                      connect=None, stop: asyncio.Event | None = None,
                      poll_interval_s: float | None = None, sleep=asyncio.sleep,
                      redis=None) -> None:
    if session_factory is None:
        from app.db.session import SessionLocal
        session_factory = SessionLocal
    client = client or get_x_stream_client()
    if not client.configured:
        logger.warning("sponsor stream disabled: LOUDRR_GATEWAY_API is not set")
        return
    interval = settings.sponsor_poll_seconds if poll_interval_s is None else poll_interval_s
    loops = [push_loop(session_factory, client, connect=connect, stop=stop, sleep=sleep)]
    if interval and interval > 0:
        loops.append(poll_loop(session_factory, client, interval_s=interval, stop=stop,
                               sleep=sleep, redis=redis))
    await asyncio.gather(*loops)
