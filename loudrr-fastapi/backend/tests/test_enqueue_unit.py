"""Unit tests for app.tasks.enqueue — no real Redis required.

The integration test in test_arq_integration.py covers the happy-path
enqueue→Redis→worker round-trip when Redis is reachable. These tests cover
the rest: the disabled-queue path, the no-redis-url path, the pool singleton
behavior, and the close_pool() teardown (idempotency + exception swallowing).

All arq.create_pool calls are mocked, so these tests run in any environment.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.tasks import enqueue as enqueue_mod


@pytest.fixture(autouse=True)
async def _reset_pool():
    """Each test starts (and ends) with a fresh module-level pool slot.

    enqueue.py keeps `_pool` as a process-wide global; without this fixture
    one test's mocked pool would leak into the next, which would mask bugs
    in the singleton path and make tests order-dependent.
    """
    enqueue_mod._pool = None
    yield
    enqueue_mod._pool = None


# ---------- use_task_queue=False: caller falls back to BackgroundTasks ----------


async def test_enqueue_returns_false_when_queue_disabled(monkeypatch):
    """With use_task_queue=False, enqueue() returns False without touching arq.

    The caller (e.g. api/claims.py) uses that False to schedule a
    BackgroundTask instead — we simulate that wiring here.
    """
    monkeypatch.setattr(enqueue_mod.settings, "use_task_queue", False)
    monkeypatch.setattr(enqueue_mod.settings, "redis_url", "redis://localhost:6379/0")

    # if create_pool is ever called we want a loud failure — it must NOT be
    fail = AsyncMock(side_effect=AssertionError("create_pool must not be called"))
    monkeypatch.setattr("arq.create_pool", fail, raising=False)

    bg = MagicMock()  # stand-in for FastAPI BackgroundTasks
    queued = await enqueue_mod.enqueue("process_verification_batch", "batch-1")
    if not queued:
        bg.add_task("process_batch_in_new_session", "batch-1")

    assert queued is False
    bg.add_task.assert_called_once_with("process_batch_in_new_session", "batch-1")


# ---------- use_task_queue=True, redis_url unset → graceful False ----------


async def test_enqueue_returns_false_when_redis_url_blank(monkeypatch):
    """use_task_queue=True but no redis_url → returns False, never imports arq."""
    monkeypatch.setattr(enqueue_mod.settings, "use_task_queue", True)
    monkeypatch.setattr(enqueue_mod.settings, "redis_url", "")

    fail = AsyncMock(side_effect=AssertionError("create_pool must not be called"))
    monkeypatch.setattr("arq.create_pool", fail, raising=False)

    queued = await enqueue_mod.enqueue("process_verification_batch", "batch-1")
    assert queued is False
    assert enqueue_mod._pool is None  # never built a pool


# ---------- use_task_queue=True + redis available → arq pool used ----------


async def test_enqueue_uses_arq_pool_when_enabled(monkeypatch):
    """Happy path: enqueue() builds the pool once and calls enqueue_job on it."""
    monkeypatch.setattr(enqueue_mod.settings, "use_task_queue", True)
    monkeypatch.setattr(enqueue_mod.settings, "redis_url", "redis://localhost:6379/0")

    fake_pool = MagicMock()
    fake_pool.enqueue_job = AsyncMock()
    fake_pool.aclose = AsyncMock()
    create_pool = AsyncMock(return_value=fake_pool)
    monkeypatch.setattr("arq.create_pool", create_pool)

    queued = await enqueue_mod.enqueue("process_verification_batch", "batch-xyz")

    assert queued is True
    create_pool.assert_awaited_once()
    fake_pool.enqueue_job.assert_awaited_once_with(
        "process_verification_batch", "batch-xyz"
    )
    assert enqueue_mod._pool is fake_pool


# ---------- singleton: shared pool across calls ----------


async def test_pool_is_singleton_across_enqueue_calls(monkeypatch):
    """create_pool must run exactly once even after many enqueue() calls."""
    monkeypatch.setattr(enqueue_mod.settings, "use_task_queue", True)
    monkeypatch.setattr(enqueue_mod.settings, "redis_url", "redis://localhost:6379/0")

    fake_pool = MagicMock()
    fake_pool.enqueue_job = AsyncMock()
    fake_pool.aclose = AsyncMock()
    create_pool = AsyncMock(return_value=fake_pool)
    monkeypatch.setattr("arq.create_pool", create_pool)

    for i in range(3):
        ok = await enqueue_mod.enqueue("task_x", f"arg-{i}")
        assert ok is True

    # pool built ONCE, reused for every enqueue
    create_pool.assert_awaited_once()
    assert fake_pool.enqueue_job.await_count == 3
    assert enqueue_mod._pool is fake_pool


# ---------- enqueue_job failure → returns False AND drops the pool ----------


async def test_enqueue_returns_false_and_drops_pool_on_error(monkeypatch):
    """If enqueue_job raises (e.g. Redis went down mid-call), the function
    catches it, returns False, and clears the cached pool so the NEXT call
    rebuilds rather than reusing a broken connection."""
    monkeypatch.setattr(enqueue_mod.settings, "use_task_queue", True)
    monkeypatch.setattr(enqueue_mod.settings, "redis_url", "redis://localhost:6379/0")

    fake_pool = MagicMock()
    fake_pool.enqueue_job = AsyncMock(side_effect=ConnectionError("redis down"))
    fake_pool.aclose = AsyncMock()
    monkeypatch.setattr("arq.create_pool", AsyncMock(return_value=fake_pool))

    queued = await enqueue_mod.enqueue("task_x", "arg")
    assert queued is False
    # close_pool() ran during the except → _pool was cleared and aclose awaited
    assert enqueue_mod._pool is None
    fake_pool.aclose.assert_awaited_once()


# ---------- close_pool: idempotent + swallows shutdown errors ----------


async def test_close_pool_is_noop_when_no_pool_opened():
    """Calling close_pool() before any enqueue() must not raise — fine to wire
    into FastAPI shutdown unconditionally."""
    assert enqueue_mod._pool is None
    await enqueue_mod.close_pool()  # must not raise
    assert enqueue_mod._pool is None
    # second call still safe
    await enqueue_mod.close_pool()
    assert enqueue_mod._pool is None


async def test_close_pool_swallows_exceptions(monkeypatch):
    """aclose() raising during shutdown must NOT propagate — the function is
    best-effort cleanup, so a broken Redis pool can't crash app shutdown."""
    monkeypatch.setattr(enqueue_mod.settings, "use_task_queue", True)
    monkeypatch.setattr(enqueue_mod.settings, "redis_url", "redis://localhost:6379/0")

    fake_pool = MagicMock()
    fake_pool.enqueue_job = AsyncMock()
    fake_pool.aclose = AsyncMock(side_effect=RuntimeError("boom during shutdown"))
    monkeypatch.setattr("arq.create_pool", AsyncMock(return_value=fake_pool))

    # build the pool by enqueueing once
    await enqueue_mod.enqueue("task_x", "arg")
    assert enqueue_mod._pool is fake_pool

    # aclose raises, but close_pool() swallows it — and still clears the slot
    await enqueue_mod.close_pool()  # must not raise
    assert enqueue_mod._pool is None
    fake_pool.aclose.assert_awaited_once()
