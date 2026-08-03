"""arq worker (Ch16) — the 7 background tasks + their schedule (spec §6).

Run with:  arq app.tasks.worker.WorkerSettings   (needs Redis).

Each task opens its own DB session and delegates to the already-built,
already-tested service logic. On-demand tasks (verification batch, tweetscout
fetch) are enqueued from request handlers; the rest run on a cron schedule.
"""
import logging

from arq import cron
from arq.connections import RedisSettings

from app.core.config import settings
from app.db.session import SessionLocal
from app.services import claims, maintenance, outbox, users

logger = logging.getLogger(__name__)


# ---- on-demand ----
async def process_verification_batch(ctx, batch_id):
    async with SessionLocal() as db:
        return await claims.run_batch(db, batch_id)


async def fetch_tweetscout_for_user(ctx, user_id):
    async with SessionLocal() as db:
        return await users.fetch_tweetscout_for_user(db, user_id)


# ---- periodic ----
async def process_pending_outbox_events(ctx):
    async with SessionLocal() as db:
        return await outbox.drain(db)


async def retry_failed_outbox_events(ctx):
    async with SessionLocal() as db:
        return await outbox.retry_failed(db)


async def cleanup_old_outbox_events(ctx):
    async with SessionLocal() as db:
        return await outbox.cleanup_old(db)


async def requeue_stuck_outbox_events(ctx):
    """Sweep OutboxEvent rows stuck in `processing` past the expected
    dispatch window. Recovers from arq worker death BETWEEN the
    PENDING→PROCESSING flip and the PROCESSING→SENT/PENDING/FAILED write
    inside `outbox.drain`. Without this, a worker crash silently strands
    Telegram notifications, waitlist mails, X-verification pings — real
    data-delivery loss with no operator signal.
    """
    async with SessionLocal() as db:
        return await outbox.requeue_stuck_processing(db)


async def reset_daily_credits(ctx):
    async with SessionLocal() as db:
        return await maintenance.reset_daily_credits(db)


async def expire_old_posts(ctx):
    async with SessionLocal() as db:
        return await maintenance.expire_old_posts(db)


async def reset_broken_streaks(ctx):
    async with SessionLocal() as db:
        return await maintenance.reset_broken_streaks(db)


async def decay_inactive_karma(ctx):
    async with SessionLocal() as db:
        return await maintenance.decay_inactive_karma(db)


async def requeue_stuck_batches(ctx):
    """Sweep VerificationBatch rows stuck in pending/processing past the
    expected processing window and re-enqueue them through the arq pool.
    Recovers from arq worker death mid-batch — without this, a user whose
    batch is stuck stays locked out of claiming forever.
    """
    from app.tasks.enqueue import enqueue

    async def _schedule(batch_id):
        await enqueue("process_verification_batch", str(batch_id))

    async with SessionLocal() as db:
        return await claims.requeue_stuck_batches(db, schedule=_schedule)


class WorkerSettings:
    redis_settings = RedisSettings.from_dsn(settings.redis_url or "redis://localhost:6379/0")
    functions = [
        process_verification_batch,
        fetch_tweetscout_for_user,
        process_pending_outbox_events,
        retry_failed_outbox_events,
        cleanup_old_outbox_events,
        requeue_stuck_outbox_events,
        reset_daily_credits,
        expire_old_posts,
        requeue_stuck_batches,
        reset_broken_streaks,
        decay_inactive_karma,
    ]
    cron_jobs = [
        # drain the outbox every minute
        cron(process_pending_outbox_events, minute=set(range(60)), run_at_startup=True),
        # retry failed outbox events hourly
        cron(retry_failed_outbox_events, minute={0}),
        # housekeeping daily
        cron(cleanup_old_outbox_events, hour={3}, minute={0}),
        # reset daily earn caps at midnight UTC
        cron(reset_daily_credits, hour={0}, minute={0}),
        # expire+refund stale posts hourly
        cron(expire_old_posts, minute={0}),
        # service-audit P1: recover from stuck verification batches every 5 min
        # (parity with Django's `requeue_stuck_batches` management command, but
        # automated — Django leaves it manual). Sweeps batches stuck in
        # pending/processing past the expected processing window.
        cron(requeue_stuck_batches, minute=set(range(0, 60, 5))),
        # sweep OutboxEvent rows stuck in `processing` — mirrors the batch
        # sweeper for the same worker-crash class of bug. Every 5 min at an
        # offset so both sweeps don't fight for the same DB locks.
        cron(requeue_stuck_outbox_events, minute=set(range(2, 60, 5))),
        # streak hygiene: zero current_streak for users who didn't engage
        # yesterday OR today. Runs 5 min after the daily-credit reset so it
        # doesn't fight for locks with reset_daily_credits.
        cron(reset_broken_streaks, hour={0}, minute={5}),
        # karma decay: deduct KARMA_DECAY_RATE * balance from users inactive
        # past KARMA_DECAY_THRESHOLD_DAYS. Daily at 02:00 UTC — offset from
        # the 00:00 reset_daily_credits and 03:00 cleanup_old_outbox_events
        # to spread DB load. Compounds for free: day N+1 sees the already-
        # decayed balance from day N.
        cron(decay_inactive_karma, hour={2}, minute={0}),
    ]
