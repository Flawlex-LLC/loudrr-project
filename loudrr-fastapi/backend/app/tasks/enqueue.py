"""Enqueue on-demand arq jobs from request handlers (Ch16).

Reuses ONE shared arq/Redis pool per process, created lazily on first use,
instead of opening and closing a fresh connection pool on every call. The
per-call create/close pattern is fine at low volume but becomes a real
bottleneck (and connection churn on Redis) under load.

Returns True if the job was enqueued, False if the queue is unavailable — the
caller then falls back to in-process BackgroundTasks.
"""
import asyncio
import logging

from app.core.config import settings

logger = logging.getLogger(__name__)

_pool = None
_pool_lock = asyncio.Lock()


async def _get_pool():
    """Lazily build one shared arq pool and reuse it across enqueues."""
    global _pool
    if _pool is None:
        async with _pool_lock:
            if _pool is None:  # double-checked: another coroutine may have built it
                from arq import create_pool
                from arq.connections import RedisSettings

                _pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    return _pool


async def enqueue(task_name: str, *args) -> bool:
    if not (settings.use_task_queue and settings.redis_url):
        return False
    try:
        pool = await _get_pool()
        await pool.enqueue_job(task_name, *args)
        return True
    except Exception as e:  # noqa: BLE001 — any failure → caller falls back
        logger.warning("arq enqueue failed for %s: %s", task_name, e)
        # drop the (possibly broken) pool so the next call rebuilds a fresh one
        await close_pool()
        return False


async def close_pool() -> None:
    """Close the shared pool — call on app shutdown, or after a failed enqueue."""
    global _pool
    if _pool is not None:
        pool, _pool = _pool, None
        try:
            await pool.aclose()
        except Exception:  # noqa: BLE001 — best-effort cleanup
            pass
