"""arq worker (Ch16) — the 7 background tasks + their schedule (spec §6).

Run with:  arq app.tasks.worker.WorkerSettings   (needs Redis).

Each task opens its own DB session and delegates to the already-built,
already-tested service logic. On-demand tasks (verification batch, tweetscout
fetch) are enqueued from request handlers; the rest run on a cron schedule.
"""
import asyncio
import contextlib
import logging

from arq import Retry, cron
from arq.connections import RedisSettings

from app.core.config import settings
from app.db.session import SessionLocal
from app.services import claims, maintenance, outbox, scores, sponsor_stream, users

logger = logging.getLogger(__name__)


# ---- on-demand ----
async def process_verification_batch(ctx, batch_id):
    async with SessionLocal() as db:
        return await claims.run_batch(db, batch_id)


async def fetch_tweetscout_for_user(ctx, user_id):
    async with SessionLocal() as db:
        return await users.fetch_tweetscout_for_user(db, user_id)


# sign-up score fetch: retry this ONE job when the provider is unavailable
# (proxy timeouts, pushback) — 1, 3 then 9 minutes later. Not a scheduler: it
# only ever re-runs the sign-up fetch that failed.
SIGNUP_SCORE_RETRY_DELAYS_S = (60, 180, 540)


async def fetch_waitlist_score(ctx, entry_id):
    """Sign-up: fetch the new applicant's score once and store it on the
    waitlist entry (the only background score fetch — no schedule)."""
    async with SessionLocal() as db:
        outcome = await scores.fetch_waitlist_score(db, entry_id)
    attempt = ctx.get("job_try", 1)
    if outcome == "unavailable" and attempt <= len(SIGNUP_SCORE_RETRY_DELAYS_S):
        raise Retry(defer=SIGNUP_SCORE_RETRY_DELAYS_S[attempt - 1])
    return outcome


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
        # same job id as the request path → a no-op if that batch is already
        # queued/running (the sweeper keys on created_at, so it can fire while
        # a long-backlogged batch is legitimately mid-flight)
        await enqueue(
            "process_verification_batch", str(batch_id), job_id=f"verify:{batch_id}",
        )

    async with SessionLocal() as db:
        return await claims.requeue_stuck_batches(db, schedule=_schedule)


async def startup(ctx):
    """Load the admin-tuned TIER_* bands. The worker is its own process: the
    API process reloads them on an admin edit, this one would otherwise pay
    out with the hardcoded defaults (run_batch also refreshes them per batch)."""
    from app.services import tier

    async with SessionLocal() as db:
        await tier.load_tiers_from_settings(db)

    # sponsored accounts: a long-lived websocket, not a job — new posts of the
    # monitored accounts become sponsored raid posts within seconds
    if settings.sponsor_stream_enabled and settings.loudrr_gateway_api:
        ctx["sponsor_stream"] = asyncio.create_task(sponsor_stream.run_forever(redis=ctx.get("redis")))


async def shutdown(ctx):
    task = ctx.get("sponsor_stream")
    if task is not None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task


class WorkerSettings:
    redis_settings = RedisSettings.from_dsn(settings.redis_url or "redis://localhost:6379/0")
    on_startup = startup
    on_shutdown = shutdown
    # Nothing reads arq job results, and keeping them (default 1h) would make
    # the deduplicating `verify:<batch_id>` job id refuse a legitimate re-run
    # — e.g. a batch held by VerificationUnavailable and re-fired by the
    # sweeper — for an hour after the first attempt finished.
    keep_result = 0
    functions = [
        process_verification_batch,
        fetch_tweetscout_for_user,
        fetch_waitlist_score,
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
        # NO score cron on purpose: scores are fetched at sign-up
        # (fetch_waitlist_score) and when the user taps Refresh score.
    ]
