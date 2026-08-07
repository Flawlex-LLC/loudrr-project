"""Integration test for the REAL queue path: enqueue() → Redis → arq worker.

The rest of the suite tests job *bodies* directly; this proves the actual
dispatch works end-to-end — a job pushed through `enqueue()` is picked up by an
arq worker (run in burst mode) and settles the batch.

Requires a running Redis. If none is reachable it skips in ~0.3s (a raw socket
probe, NOT arq's slow retrying connect), so the suite stays green without infra.
Run a throwaway Redis to exercise it:  docker run --rm -p 6379:6379 redis:7
"""
import socket
from decimal import Decimal
from urllib.parse import urlparse

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings as app_settings
from app.models.engagement import Engagement
from app.models.post import Post
from app.models.verification_batch import VerificationBatch

_REDIS_DSN = app_settings.redis_url or "redis://localhost:6379/0"
_u = urlparse(_REDIS_DSN)
_HOST, _PORT = _u.hostname or "localhost", _u.port or 6379
TEST_DATABASE_URL = app_settings.database_url.rsplit("/", 1)[0] + "/loudrr_test"


def _redis_reachable() -> bool:
    try:
        with socket.create_connection((_HOST, _PORT), timeout=0.3):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(
    not _redis_reachable(),
    reason=f"Redis not reachable at {_HOST}:{_PORT} — arq integration test skipped",
)


async def test_enqueue_then_worker_settles_batch(db_session, make_user, monkeypatch):
    from arq.connections import RedisSettings
    from arq.worker import Worker

    from app.tasks import enqueue as enqueue_mod
    from app.tasks import worker as worker_mod

    # --- arrange data in loudrr_test: a viewer engaged a post, queued in a batch ---
    owner = await make_user(telegram_id=12_001)
    viewer = await make_user(telegram_id=12_002, x_username="v")
    # empty tweet_id + a link with no /status/ → "benefit of doubt" pass, so the
    # worker settles WITHOUT any Twitter call (keeps this test infra-light)
    post = Post(
        user_id=owner.id, x_link="https://x.com/owner", tweet_id="",
        escrow=Decimal("50"), initial_escrow=Decimal("50"), status="active", platform="web",
    )
    db_session.add(post)
    await db_session.commit()
    eng = Engagement(user_id=viewer.id, post_id=post.id)
    db_session.add(eng)
    await db_session.commit()
    batch = VerificationBatch(
        user_id=viewer.id, engagement_ids=[str(eng.id)], status="pending"
    )
    db_session.add(batch)
    await db_session.commit()
    batch_id = batch.id

    # the worker job opens its own SessionLocal (bound to the dev DB) — point it
    # at loudrr_test so it settles the rows we just created
    test_engine = create_async_engine(TEST_DATABASE_URL)
    monkeypatch.setattr(
        worker_mod, "SessionLocal",
        async_sessionmaker(test_engine, expire_on_commit=False),
    )

    # turn the queue ON and enqueue through the REAL shared-pool enqueue()
    monkeypatch.setattr(enqueue_mod.settings, "use_task_queue", True)
    monkeypatch.setattr(enqueue_mod.settings, "redis_url", _REDIS_DSN)
    queued = await enqueue_mod.enqueue("process_verification_batch", str(batch_id))
    assert queued is True  # the job really went onto Redis

    # --- act: run a worker in burst mode; it drains the queue then returns ---
    w = Worker(
        functions=[worker_mod.process_verification_batch],
        redis_settings=RedisSettings.from_dsn(_REDIS_DSN),
        burst=True,
        poll_delay=0.0,
    )
    try:
        await w.main()
    finally:
        await w.close()
        await enqueue_mod.close_pool()
        await test_engine.dispose()

    # --- assert: the worker (not this test) settled the batch ---
    fresh = (
        await db_session.execute(
            select(VerificationBatch).where(VerificationBatch.id == batch_id)
        )
    ).scalar_one()
    await db_session.refresh(fresh)
    assert fresh.status == "completed"
    assert fresh.passed == 1
    assert fresh.credits_awarded == Decimal("1.0000")
