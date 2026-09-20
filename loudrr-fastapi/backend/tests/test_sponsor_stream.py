"""Sponsored-account listener (services/sponsor_stream.py) + its worker wiring.

No real websocket or gateway: `connect` is a fake that hands out scripted
connections, the client is a fake, and `sleep` is injected so backoff is
recorded instead of waited out.
"""
import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import settings
from app.core.time_utils import utcnow
from app.integrations.x_stream import GatewayUnavailable
from app.models.post import Post
from app.models.sponsored_account import SponsoredAccount
from app.services import sponsor_stream, sponsors


# ---------------------------------------------------------------------------
# fakes
# ---------------------------------------------------------------------------
class FakeGateway:
    def __init__(self, *, configured=True, monitors=(), fail=()):
        self.configured = configured
        self.monitors = list(monitors)
        self.fail = set(fail)
        self.calls: list[tuple] = []

    def websocket_url(self):
        return "wss://gateway.test/twitter/tweet/websocket"

    def headers(self):
        return {"x-api-key": "test-key"}

    async def list_monitors(self):
        self.calls.append(("list_monitors",))
        if "list_monitors" in self.fail:
            raise GatewayUnavailable("down")
        return [{"x_user_screen_name": m} for m in self.monitors]

    async def add_monitor(self, username):
        self.calls.append(("add_monitor", username))

    async def remove_monitor(self, username):
        self.calls.append(("remove_monitor", username))
        return True

    async def users_by_ids(self, ids):
        self.calls.append(("users_by_ids", tuple(ids)))
        return []


class FakeWebSocket:
    """recv() hands out the scripted items in order: a str/bytes frame, an
    exception to raise, or a zero-arg callable (run, then its return value is
    the frame). When the script runs out the connection 'closes'."""

    def __init__(self, *items):
        self.items = list(items)

    async def recv(self):
        await asyncio.sleep(0)  # a real recv yields to the loop
        if not self.items:
            raise ConnectionError("connection closed")
        item = self.items.pop(0)
        if isinstance(item, BaseException):
            raise item
        if callable(item):
            item = item()
            if asyncio.iscoroutine(item):
                item = await item
        return item


class FakeConnect:
    """Stands in for websockets.connect: each call takes the next scripted
    connection (a FakeWebSocket, or an exception the handshake raises)."""

    def __init__(self, *connections):
        self.connections = list(connections)
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url, **kwargs):
        self.calls.append((url, kwargs))
        item = self.connections.pop(0) if self.connections else OSError("handshake failed")

        @asynccontextmanager
        async def _cm():
            if isinstance(item, BaseException):
                raise item
            yield item

        return _cm()


class Done(Exception):
    """Raised by a fake sleep to end a loop that would otherwise run forever."""


def recording_sleep(delays: list, *, stop_after: int):
    async def _sleep(seconds):
        delays.append(seconds)
        await asyncio.sleep(0)
        if len(delays) >= stop_after:
            raise Done()
    return _sleep


def shared_session(db):
    """A session_factory that hands out the test session without closing it."""
    @asynccontextmanager
    async def _factory():
        yield db
    return _factory


def tweet_frame(*tweet_ids, user="acme", author_id="111"):
    created = (utcnow() - timedelta(minutes=1)).strftime("%a %b %d %H:%M:%S +0000 %Y")
    return json.dumps({
        "event_type": "tweet",
        "tweets": [
            {"id": tid, "text": f"post {tid}", "createdAt": created,
             "author": {"id": author_id, "userName": user, "name": "Acme"}}
            for tid in tweet_ids
        ],
    })


PING = json.dumps({"event_type": "ping", "timestamp": 1})
CONNECTED = json.dumps({"event_type": "connected"})


async def make_sponsor(db, handle="acme", **overrides):
    values = dict(x_username=handle, x_user_id="111", karma_per_post=Decimal("100"),
                  active_since=utcnow() - timedelta(hours=1))
    values.update(overrides)
    sponsor = SponsoredAccount(**values)
    db.add(sponsor)
    await db.commit()
    return sponsor


async def post_tweet_ids(db) -> list[str]:
    return sorted(
        (await db.execute(select(Post.tweet_id).where(Post.platform == "sponsor"))).scalars().all()
    )


@pytest.fixture
def sync_calls(monkeypatch):
    """Replace the connect-time monitor sync with a recorder (the real one is
    covered below, and would share the test session with frame ingest)."""
    calls = []

    async def _fake_sync(session_factory, client):
        calls.append(client)

    monkeypatch.setattr(sponsor_stream, "sync_monitors", _fake_sync)
    return calls


# ---------------------------------------------------------------------------
# handle_frame
# ---------------------------------------------------------------------------
async def test_handle_frame_ingests_tweets(db_session):
    await make_sponsor(db_session)
    factory = shared_session(db_session)
    assert await sponsor_stream.handle_frame(tweet_frame("100", "101"), factory) == 2
    assert await sponsor_stream.handle_frame(tweet_frame("101"), factory) == 0  # already in
    assert await sponsor_stream.handle_frame(tweet_frame("102").encode(), factory) == 1  # bytes frame
    assert await post_tweet_ids(db_session) == ["100", "101", "102"]


@pytest.mark.parametrize("raw", [PING, CONNECTED, "not json", "", None, "[1, 2]", '"str"'])
async def test_handle_frame_ignores_non_tweet_frames(raw):
    def _factory():
        raise AssertionError("no session should be opened for a frame without tweets")

    assert await sponsor_stream.handle_frame(raw, _factory) == 0


# ---------------------------------------------------------------------------
# sync_monitors wrapper
# ---------------------------------------------------------------------------
async def test_stream_sync_monitors_runs_service_sync(db_session):
    await make_sponsor(db_session, handle="acme")
    gw = FakeGateway(monitors=[])
    await sponsor_stream.sync_monitors(shared_session(db_session), gw)
    assert ("add_monitor", "acme") in gw.calls


async def test_stream_sync_monitors_swallows_gateway_errors(db_session):
    await make_sponsor(db_session, handle="acme")
    gw = FakeGateway(fail={"list_monitors"})
    await sponsor_stream.sync_monitors(shared_session(db_session), gw)  # no raise
    assert gw.calls == [("list_monitors",)]


# ---------------------------------------------------------------------------
# push_loop
# ---------------------------------------------------------------------------
async def test_push_loop_ingests_frames_and_reconnects(db_session, sync_calls):
    await make_sponsor(db_session)
    gw = FakeGateway()
    connect = FakeConnect(
        FakeWebSocket(CONNECTED, PING, tweet_frame("100"), ConnectionError("dropped")),
        FakeWebSocket(CONNECTED, tweet_frame("101", "100"), "garbage"),  # then closes
    )
    delays: list = []

    with pytest.raises(Done):
        await sponsor_stream.push_loop(
            shared_session(db_session), gw, connect=connect,
            sleep=recording_sleep(delays, stop_after=2),
        )

    assert await post_tweet_ids(db_session) == ["100", "101"]
    # connected twice, with the gateway's url + key, and synced monitors each time
    assert len(connect.calls) == 2
    url, kwargs = connect.calls[0]
    assert url == "wss://gateway.test/twitter/tweet/websocket"
    assert kwargs["additional_headers"] == {"x-api-key": "test-key"}
    assert len(sync_calls) == 2 and sync_calls[0] is gw
    # backoff between reconnects doubles
    assert delays == [1, 2]


def test_real_connect_accepts_the_push_loop_options():
    """push_loop's fakes accept anything; the real websockets.connect must
    take these as named options (its **kwargs would pass an unknown one down
    to loop.create_connection and fail every handshake)."""
    import inspect

    import websockets

    params = inspect.signature(websockets.connect).parameters
    for name in ("additional_headers", "open_timeout", "max_size"):
        assert name in params, name


async def test_push_loop_backoff_doubles_and_caps(sync_calls):
    connect = FakeConnect()  # every handshake fails
    delays: list = []

    def _factory():
        raise AssertionError("no frames, no sessions")

    with pytest.raises(Done):
        await sponsor_stream.push_loop(
            _factory, FakeGateway(), connect=connect, sleep=recording_sleep(delays, stop_after=9),
        )

    assert delays == [1, 2, 4, 8, 16, 32, 60, 60, 60]
    assert len(connect.calls) == 9
    assert sync_calls == []  # never connected


async def test_push_loop_backoff_resets_after_a_long_connection(monkeypatch, sync_calls):
    clock = iter([1000.0, 1100.0])  # connected at 1000, dropped 100s later
    connect = FakeConnect(OSError("refused"), OSError("refused"), FakeWebSocket(PING))
    delays: list = []

    with pytest.raises(Done):
        await sponsor_stream.push_loop(
            shared_session(None), FakeGateway(), connect=connect,
            sleep=recording_sleep(delays, stop_after=4), clock=lambda: next(clock),
        )

    # 1, 2 while refused; the connection that lived > 60s resets it to 1
    assert delays == [1, 2, 1, 2]


async def test_push_loop_bad_frame_keeps_the_connection(monkeypatch, sync_calls):
    handled = []

    async def _flaky_handle(raw, session_factory):
        handled.append(raw)
        if len(handled) == 1:
            raise RuntimeError("db hiccup")
        return 1

    monkeypatch.setattr(sponsor_stream, "handle_frame", _flaky_handle)
    connect = FakeConnect(FakeWebSocket("frame-1", "frame-2", "frame-3"))
    delays: list = []

    with pytest.raises(Done):
        await sponsor_stream.push_loop(
            shared_session(None), FakeGateway(), connect=connect,
            sleep=recording_sleep(delays, stop_after=1),
        )

    assert handled == ["frame-1", "frame-2", "frame-3"]
    assert len(connect.calls) == 1


async def test_push_loop_silent_connection_is_replaced(monkeypatch, sync_calls):
    monkeypatch.setattr(sponsor_stream, "RECV_TIMEOUT_S", 0.05)
    never = asyncio.Event()
    connect = FakeConnect(FakeWebSocket(never.wait), FakeWebSocket(never.wait))
    delays: list = []

    with pytest.raises(Done):
        await sponsor_stream.push_loop(
            shared_session(None), FakeGateway(), connect=connect,
            sleep=recording_sleep(delays, stop_after=2),
        )

    assert len(connect.calls) == 2
    assert delays == [1, 2]


async def test_push_loop_exits_when_stop_is_set(db_session, sync_calls):
    await make_sponsor(db_session)
    stop = asyncio.Event()
    connect = FakeConnect(
        FakeWebSocket(tweet_frame("100"), lambda: stop.set() or PING, tweet_frame("999")),
    )

    async def _no_sleep(seconds):
        raise AssertionError("must not back off once stopped")

    await asyncio.wait_for(
        sponsor_stream.push_loop(shared_session(db_session), FakeGateway(), connect=connect,
                                 stop=stop, sleep=_no_sleep),
        timeout=5,
    )

    assert len(connect.calls) == 1
    assert await post_tweet_ids(db_session) == ["100"]  # the frame after stop never read


async def test_push_loop_stop_interrupts_backoff(sync_calls):
    stop = asyncio.Event()
    connect = FakeConnect()  # every handshake fails

    async def _stop_soon():
        await asyncio.sleep(0.05)
        stop.set()

    loop = asyncio.get_running_loop()
    started = loop.time()
    stopper = asyncio.create_task(_stop_soon())
    await asyncio.wait_for(
        sponsor_stream.push_loop(shared_session(None), FakeGateway(), connect=connect, stop=stop),
        timeout=5,
    )
    await stopper
    assert len(connect.calls) == 1
    assert loop.time() - started < 0.9  # woke from the 1s backoff as soon as stop was set


async def test_push_loop_stop_set_during_handshake(sync_calls):
    stop = asyncio.Event()

    class _StopWhileConnecting(FakeConnect):
        def __call__(self, url, **kwargs):
            stop.set()
            return super().__call__(url, **kwargs)

    connect = _StopWhileConnecting()  # the handshake fails, and we're stopping
    await asyncio.wait_for(
        sponsor_stream.push_loop(shared_session(None), FakeGateway(), connect=connect, stop=stop),
        timeout=0.5,  # no backoff wait at all
    )
    assert len(connect.calls) == 1


async def test_push_loop_already_stopped_never_connects(sync_calls):
    stop = asyncio.Event()
    stop.set()
    connect = FakeConnect()
    await sponsor_stream.push_loop(shared_session(None), FakeGateway(), connect=connect, stop=stop)
    assert connect.calls == []


async def test_push_loop_end_to_end_with_real_monitor_sync(db_session, monkeypatch):
    """No recorder: the connect-time sync runs for real on its own session
    while frames are ingested on others."""
    await make_sponsor(db_session, handle="acme", x_user_id="111")
    await make_sponsor(db_session, handle="paused", x_user_id="222", is_active=False)
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    synced = asyncio.Event()
    real_sync = sponsor_stream.sync_monitors

    async def _sync_then_signal(session_factory, client):
        await real_sync(session_factory, client)
        synced.set()

    monkeypatch.setattr(sponsor_stream, "sync_monitors", _sync_then_signal)
    gw = FakeGateway(monitors=["paused"])

    async def _after_sync():
        await asyncio.wait_for(synced.wait(), timeout=5)
        return tweet_frame("100")

    connect = FakeConnect(FakeWebSocket(CONNECTED, _after_sync))
    delays: list = []

    with pytest.raises(Done):
        await sponsor_stream.push_loop(factory, gw, connect=connect,
                                       sleep=recording_sleep(delays, stop_after=1))

    assert ("add_monitor", "acme") in gw.calls
    assert ("remove_monitor", "paused") in gw.calls
    assert await post_tweet_ids(db_session) == ["100"]


# ---------------------------------------------------------------------------
# poll_loop
# ---------------------------------------------------------------------------
async def test_poll_loop_keeps_window_on_failure_and_advances_on_success(db_session, monkeypatch):
    await make_sponsor(db_session, active_since=utcnow() - timedelta(hours=3))
    seen = []
    outcomes = [GatewayUnavailable("down"), 2, RuntimeError("bug"), 0]

    async def _fake_poll(db, client, *, since):
        seen.append(since)
        outcome = outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(sponsors, "poll_new_posts", _fake_poll)
    delays: list = []

    with pytest.raises(Done):
        await sponsor_stream.poll_loop(
            shared_session(db_session), FakeGateway(), interval_s=20,
            sleep=recording_sleep(delays, stop_after=4),
        )

    first, after_failure, after_success, after_bug = seen
    # the first window starts where poll_since says (the sponsor's active_since),
    # minus the overlap
    expected_first = utcnow() - timedelta(hours=3) - sponsor_stream.POLL_OVERLAP
    assert abs((first - expected_first).total_seconds()) < 5
    # a gateway failure keeps the window, so the next poll re-covers it
    assert after_failure == first
    # a successful poll moves it up to (its start - overlap)
    expected = utcnow() - sponsor_stream.POLL_OVERLAP
    assert after_success > first
    assert abs((after_success - expected).total_seconds()) < 5
    # any other failure also keeps the window
    assert after_bug == after_success
    # a failed poll backs off (doubling), a successful one returns to the interval
    assert delays == [40, 20, 40, 20]


async def test_poll_loop_window_never_older_than_max_tweet_age(db_session, monkeypatch):
    seen = []

    async def _fake_poll(db, client, *, since):
        seen.append(since)
        return 0

    async def _ancient(db):
        return utcnow() - timedelta(days=5)

    monkeypatch.setattr(sponsors, "poll_new_posts", _fake_poll)
    monkeypatch.setattr(sponsors, "poll_since", _ancient)
    monkeypatch.setattr(sponsor_stream, "POLL_OVERLAP", timedelta(days=2))

    with pytest.raises(Done):
        await sponsor_stream.poll_loop(shared_session(db_session), FakeGateway(), interval_s=5,
                                       sleep=recording_sleep([], stop_after=2))

    floor = utcnow() - sponsors.MAX_TWEET_AGE
    assert abs((seen[1] - floor).total_seconds()) < 5


async def test_poll_loop_real_poll_imports_posts(db_session):
    await make_sponsor(db_session)
    created = (utcnow() - timedelta(minutes=1)).strftime("%a %b %d %H:%M:%S +0000 %Y")
    queries = []

    class _Gw(FakeGateway):
        async def search_latest(self, query, cursor=""):
            queries.append(query)
            return ([{"id": "700", "text": "hi", "createdAt": created,
                      "author": {"id": "111", "userName": "acme"}}], "", False)

    with pytest.raises(Done):
        await sponsor_stream.poll_loop(shared_session(db_session), _Gw(), interval_s=1,
                                       sleep=recording_sleep([], stop_after=2))

    assert len(queries) == 2 and all("from:acme" in q for q in queries)
    assert await post_tweet_ids(db_session) == ["700"]


async def test_poll_loop_exits_when_stopped(db_session, monkeypatch):
    stop = asyncio.Event()
    calls = []

    async def _fake_poll(db, client, *, since):
        calls.append(since)
        stop.set()
        return 0

    monkeypatch.setattr(sponsors, "poll_new_posts", _fake_poll)
    await asyncio.wait_for(
        sponsor_stream.poll_loop(shared_session(db_session), FakeGateway(), interval_s=30, stop=stop),
        timeout=5,
    )
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# run_forever
# ---------------------------------------------------------------------------
@pytest.fixture
def loops(monkeypatch):
    calls = {"push": [], "poll": []}

    async def _push(session_factory, client, **kwargs):
        calls["push"].append(kwargs)

    async def _poll(session_factory, client, **kwargs):
        calls["poll"].append(kwargs)

    monkeypatch.setattr(sponsor_stream, "push_loop", _push)
    monkeypatch.setattr(sponsor_stream, "poll_loop", _poll)
    return calls


async def test_run_forever_returns_when_gateway_not_configured(loops):
    connect = FakeConnect()
    await asyncio.wait_for(
        sponsor_stream.run_forever(session_factory=shared_session(None),
                                   client=FakeGateway(configured=False), connect=connect),
        timeout=1,
    )
    assert loops == {"push": [], "poll": []}
    assert connect.calls == []


async def test_run_forever_default_client_unconfigured(loops, monkeypatch):
    monkeypatch.setattr(settings, "loudrr_gateway_api", "")
    await asyncio.wait_for(sponsor_stream.run_forever(session_factory=shared_session(None)), timeout=1)
    assert loops == {"push": [], "poll": []}


async def test_run_forever_push_only_when_interval_zero(loops):
    await sponsor_stream.run_forever(session_factory=shared_session(None), client=FakeGateway(),
                                     poll_interval_s=0)
    assert len(loops["push"]) == 1
    assert loops["poll"] == []


async def test_run_forever_starts_both_loops(loops, monkeypatch):
    monkeypatch.setattr(settings, "sponsor_poll_seconds", 7)
    stop = asyncio.Event()
    connect = FakeConnect()
    await sponsor_stream.run_forever(session_factory=shared_session(None), client=FakeGateway(),
                                     connect=connect, stop=stop)
    assert len(loops["push"]) == 1 and loops["push"][0]["connect"] is connect
    assert loops["push"][0]["stop"] is stop
    assert len(loops["poll"]) == 1 and loops["poll"][0]["interval_s"] == 7


# ---------------------------------------------------------------------------
# worker wiring
# ---------------------------------------------------------------------------
def test_worker_settings_lifecycle_hooks():
    from app.tasks import worker

    assert worker.WorkerSettings.on_startup is worker.startup
    assert worker.WorkerSettings.on_shutdown is worker.shutdown
    # the stream is a long-lived task, not an arq job or cron
    names = {f.__name__ for f in worker.WorkerSettings.functions}
    assert not any("sponsor" in n for n in names)
    assert not any("sponsor" in job.name for job in worker.WorkerSettings.cron_jobs)


@pytest.fixture
def worker_env(monkeypatch):
    """startup() with no real DB and a stand-in stream."""
    from app.services import tier
    from app.tasks import worker

    started = []

    async def _run_forever(**kwargs):
        started.append(kwargs)
        await asyncio.Event().wait()  # like the real one: runs until cancelled

    async def _no_tiers(db):
        return True

    monkeypatch.setattr(worker, "SessionLocal", shared_session(None))
    monkeypatch.setattr(tier, "load_tiers_from_settings", _no_tiers)
    monkeypatch.setattr(worker.sponsor_stream, "run_forever", _run_forever)
    return worker, started


@pytest.mark.parametrize("enabled, key", [(False, "gateway-key"), (True, ""), (False, "")])
async def test_worker_startup_skips_stream(worker_env, monkeypatch, enabled, key):
    worker, started = worker_env
    monkeypatch.setattr(settings, "sponsor_stream_enabled", enabled)
    monkeypatch.setattr(settings, "loudrr_gateway_api", key)
    ctx = {}
    await worker.startup(ctx)
    await asyncio.sleep(0)
    assert "sponsor_stream" not in ctx
    assert started == []
    await worker.shutdown(ctx)  # nothing to cancel — no error


async def test_worker_startup_starts_and_shutdown_cancels_stream(worker_env, monkeypatch):
    worker, started = worker_env
    monkeypatch.setattr(settings, "sponsor_stream_enabled", True)
    monkeypatch.setattr(settings, "loudrr_gateway_api", "gateway-key")
    redis = object()
    ctx = {"redis": redis}
    await worker.startup(ctx)
    task = ctx["sponsor_stream"]
    await asyncio.sleep(0)
    assert started == [{"redis": redis}] and not task.done()

    await worker.shutdown(ctx)
    assert task.cancelled()


async def test_worker_shutdown_tolerates_a_crashed_stream():
    from app.tasks import worker

    async def _boom():
        raise RuntimeError("stream crashed")

    task = asyncio.create_task(_boom())
    await asyncio.sleep(0)
    await worker.shutdown({"sponsor_stream": task})  # no raise
    assert task.done()



# ---------------------------------------------------------------------------
# review fixes: sync only on a confirmed connection + periodically,
# restart watermark, sweep, backoff, handle refresh
# ---------------------------------------------------------------------------
async def test_push_loop_no_sync_when_the_gateway_rejects_the_connection(sync_calls):
    """A duplicate connection (1008) is closed before "connected": it must not
    rewrite the shared monitor list."""
    connect = FakeConnect(FakeWebSocket(ConnectionError("1008 duplicate connection")))
    with pytest.raises(Done):
        await sponsor_stream.push_loop(shared_session(None), FakeGateway(), connect=connect,
                                       sleep=recording_sleep([], stop_after=1))
    assert sync_calls == []


async def test_push_loop_resyncs_periodically(sync_calls):
    now = [0.0]

    def _tick():
        now[0] += 100  # every frame is 100s later
        return PING

    frames = [CONNECTED] + [_tick] * 13
    connect = FakeConnect(FakeWebSocket(*frames))
    with pytest.raises(Done):
        await sponsor_stream.push_loop(shared_session(None), FakeGateway(), connect=connect,
                                       sleep=recording_sleep([], stop_after=1), clock=lambda: now[0])
    # on "connected", then again every SYNC_INTERVAL_S (600s) of pings
    assert len(sync_calls) == 3


class FakeRedis:
    def __init__(self, initial=None):
        self.store = dict(initial or {})
        self.sets = []

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self.store[key] = value.encode()
        self.sets.append((key, value, ex))


async def test_poll_loop_resumes_from_the_saved_watermark(db_session, monkeypatch):
    await make_sponsor(db_session, active_since=utcnow() - timedelta(hours=6))
    mark = utcnow() - timedelta(minutes=7)
    redis = FakeRedis({sponsor_stream.WATERMARK_KEY: mark.isoformat().encode()})
    seen = []

    async def _fake_poll(db, client, *, since):
        seen.append(since)
        return 0

    monkeypatch.setattr(sponsors, "poll_new_posts", _fake_poll)
    with pytest.raises(Done):
        await sponsor_stream.poll_loop(shared_session(db_session), FakeGateway(), interval_s=20,
                                       sleep=recording_sleep([], stop_after=1), redis=redis)

    # the restart picks up 7 minutes back (minus overlap), not the sponsor's 6h
    assert seen == [mark - sponsor_stream.POLL_OVERLAP]
    [(key, value, ex)] = redis.sets
    assert key == sponsor_stream.WATERMARK_KEY
    assert abs((utcnow() - datetime.fromisoformat(value)).total_seconds()) < 5
    assert ex == int(sponsors.MAX_TWEET_AGE.total_seconds())


async def test_poll_loop_unreadable_watermark_falls_back(db_session, monkeypatch):
    await make_sponsor(db_session, active_since=utcnow() - timedelta(hours=2))

    class _BrokenRedis:
        async def get(self, key):
            raise ConnectionError("redis down")

        async def set(self, key, value, ex=None):
            raise ConnectionError("redis down")

    seen = []

    async def _fake_poll(db, client, *, since):
        seen.append(since)
        return 0

    monkeypatch.setattr(sponsors, "poll_new_posts", _fake_poll)
    delays: list = []
    with pytest.raises(Done):
        await sponsor_stream.poll_loop(shared_session(db_session), FakeGateway(), interval_s=20,
                                       sleep=recording_sleep(delays, stop_after=1), redis=_BrokenRedis())
    expected = utcnow() - timedelta(hours=2) - sponsor_stream.POLL_OVERLAP
    assert abs((seen[0] - expected).total_seconds()) < 5
    assert delays == [20]  # a Redis problem is not a failed poll


async def test_poll_loop_sweeps_a_wider_window_periodically(db_session, monkeypatch):
    seen = []
    now = [0.0]

    async def _fake_poll(db, client, *, since):
        seen.append(since)
        now[0] += 100  # each poll cycle is 100s of monotonic time
        return 0

    async def _now(db):
        return utcnow()

    monkeypatch.setattr(sponsors, "poll_new_posts", _fake_poll)
    monkeypatch.setattr(sponsors, "poll_since", _now)
    with pytest.raises(Done):
        await sponsor_stream.poll_loop(shared_session(db_session), FakeGateway(), interval_s=20,
                                       sleep=recording_sleep([], stop_after=8), clock=lambda: now[0])

    widths = [(utcnow() - s).total_seconds() for s in seen]
    wide = [i for i, w in enumerate(widths) if w > 300]
    # a normal window is ~overlap wide; once SWEEP_EVERY_S (300s) has passed since
    # the last completed sweep, one poll covers 10 minutes (polls start at 0, 100, ..)
    assert wide == [3, 7]
    assert all(w < 120 for i, w in enumerate(widths) if i not in wide)


async def test_poll_loop_backoff_caps(db_session, monkeypatch):
    async def _down(db, client, *, since):
        raise GatewayUnavailable("402 no credits")

    monkeypatch.setattr(sponsors, "poll_new_posts", _down)
    delays: list = []
    with pytest.raises(Done):
        await sponsor_stream.poll_loop(shared_session(db_session), FakeGateway(), interval_s=20,
                                       sleep=recording_sleep(delays, stop_after=6))
    assert delays == [40, 80, 160, 300, 300, 300]


async def test_poll_loop_refreshes_handles_hourly_and_survives_its_failure(db_session, monkeypatch):
    refreshes = []
    now = [0.0]

    async def _refresh(db, client):
        refreshes.append(now[0])
        if len(refreshes) == 1:
            raise GatewayUnavailable("down")
        return []

    async def _fake_poll(db, client, *, since):
        now[0] += 1000
        return 0

    monkeypatch.setattr(sponsors, "refresh_handles", _refresh)
    monkeypatch.setattr(sponsors, "poll_new_posts", _fake_poll)
    delays: list = []
    with pytest.raises(Done):
        await sponsor_stream.poll_loop(shared_session(db_session), FakeGateway(), interval_s=20,
                                       sleep=recording_sleep(delays, stop_after=5), clock=lambda: now[0])
    # at start, then once 3600s have passed (polls at 0, 1000, 2000, 3000, 4000)
    assert refreshes == [0.0, 4000.0]
    assert delays == [20, 20, 20, 20, 20]  # the failed refresh didn't fail the poll
