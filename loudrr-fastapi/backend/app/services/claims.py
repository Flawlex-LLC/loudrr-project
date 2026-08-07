"""The claim queue (Ch13) — endpoints 12, 13 + the batch processor.

`/session/queue-claim/` checks the gates, snapshots the pending engagements
into a batch, and schedules processing. `run_batch` runs Phase 1 (verify) then
Phase 2 (settle) and records the outcome. Until arq + Redis arrive (Ch16), the
batch runs via FastAPI BackgroundTasks in its own session — `run_batch` itself
is queue-agnostic, so Ch16 only swaps how it's dispatched.
"""
import logging
import uuid
from decimal import Decimal

from sqlalchemy import func, select

from app.core.errors import Forbidden
from app.core.time_utils import utcnow
from app.db.session import SessionLocal
from app.models.engagement import Engagement
from app.models.post import Post
from app.models.user import User
from app.models.verification_batch import BatchStatus, VerificationBatch
from app.integrations.twitter import extract_tweet_id
from app.repositories.verification_batch import VerificationBatchRepository
from app.services import settlement, verification
from app.services.site_settings import get_setting
from app.services.verification import ToVerify

logger = logging.getLogger(__name__)


async def _pending_count(db, user_id) -> int:
    q = select(func.count()).select_from(Engagement).where(
        Engagement.user_id == user_id,
        Engagement.verified.is_(False),
        Engagement.credit_granted.is_(False),
    )
    return int((await db.execute(q)).scalar_one())


# ---- endpoint 12: POST /session/queue-claim/ ----
async def queue_claim(db, *, user, schedule) -> tuple[dict, int]:
    """Returns (body, http_status). `schedule(batch_id)` enqueues processing."""
    if user.is_banned:
        raise Forbidden("Your account has been suspended")
    if not user.x_username:
        return (
            {"success": False, "error": "x_account_required",
             "message": "Please link your X account before claiming rewards."},
            400,
        )

    min_to_claim = await get_setting(db, "MIN_ENGAGEMENTS_TO_CLAIM", 10)
    pending = (
        await db.execute(
            select(Engagement)
            .where(
                Engagement.user_id == user.id,
                Engagement.verified.is_(False),
                Engagement.credit_granted.is_(False),
            )
            .order_by(Engagement.clicked_at)
        )
    ).scalars().all()

    # VERIFICATION_BATCH_SIZE — cap on engagements processed per batch (parity
    # with Django seeded setting; default 0 = no cap, take everything). When
    # set, only the oldest N pending engagements ride this batch; the rest
    # stay queued for the next /session/queue-claim/ call. The min_to_claim
    # gate is still applied to the FULL pending count so the user can claim
    # as long as they meet the threshold across batches.
    batch_size = int(await get_setting(db, "VERIFICATION_BATCH_SIZE", 0))

    if len(pending) < min_to_claim:
        return (
            {"success": False,
             "message": f"Need {min_to_claim}+ engagements to claim. You have {len(pending)}.",
             "pending_count": len(pending)},
            200,
        )

    # anti-gaming: enforce a minimum wait since the first pending click
    min_duration = await get_setting(db, "MIN_SESSION_DURATION_SECONDS", 30)
    if min_duration > 0 and pending:
        elapsed = (utcnow() - pending[0].clicked_at).total_seconds()
        if elapsed < min_duration:
            remaining = int(min_duration - elapsed)
            return (
                {"success": False, "error": "insufficient_engagement_time",
                 "message": f"Please wait {remaining} seconds before claiming.",
                 "pending_count": len(pending), "remaining_seconds": remaining},
                200,
            )

    # position = number of in-flight batches ahead of this one + 1
    inflight = int(
        (
            await db.execute(
                select(func.count())
                .select_from(VerificationBatch)
                .where(VerificationBatch.status.in_(["pending", "processing"]))
            )
        ).scalar_one()
    )

    # slice to the configured batch size (oldest-first preserves FIFO order so
    # the next claim picks up the leftovers in the right order)
    snapshot = pending if batch_size <= 0 else pending[:batch_size]
    engagement_ids = [str(e.id) for e in snapshot]
    batch = await VerificationBatchRepository(db).create(
        user_id=user.id, engagement_ids=engagement_ids, status=BatchStatus.PENDING.value,
    )
    await db.commit()

    await schedule(batch.id)

    return (
        {"success": True, "batch_id": str(batch.id), "status": "pending",
         "position": inflight + 1, "engagement_count": len(engagement_ids),
         "message": "Verification queued! You can continue engaging."},
        200,
    )


# ---- endpoint 13: GET /claims/history/ ----
async def claim_history(db, *, user) -> dict:
    batches = (
        await db.execute(
            select(VerificationBatch)
            .where(VerificationBatch.user_id == user.id)
            .order_by(VerificationBatch.created_at.desc())
            .limit(20)
        )
    ).scalars().all()

    batch_list = [
        {
            "id": str(b.id),
            "status": b.status,
            "engagement_count": len(b.engagement_ids or []),
            "passed": b.passed,
            "failed": b.failed,
            "credits_awarded": float(b.credits_awarded) if b.credits_awarded is not None else None,
            "message": b.message,
            "created_at": b.created_at.isoformat(),
            "completed_at": b.completed_at.isoformat() if b.completed_at else None,
        }
        for b in batches
    ]
    return {
        "batches": batch_list,
        "pending_engagements": await _pending_count(db, user.id),
        "has_processing": any(b["status"] in ("pending", "processing") for b in batch_list),
    }


# ---- the batch processor (Phase 1 + Phase 2) ----
async def _finish(db, batch, *, passed, failed, awarded, message) -> dict:
    batch.status = BatchStatus.COMPLETED.value
    batch.passed = passed
    batch.failed = failed
    batch.credits_awarded = awarded
    batch.message = message
    batch.completed_at = utcnow()
    # queue the "claim_completed" Telegram card in THIS transaction so the
    # user learns the outcome of their batch without polling /claims/history/
    from app.repositories.user import UserRepository
    from app.services.outbox import OutboxService
    user = await UserRepository(db).get(id=batch.user_id)
    if user is not None and user.telegram_id is not None:
        await OutboxService.queue_claim_completed(
            db, batch_id=batch.id, user_id=batch.user_id,
            telegram_id=user.telegram_id, passed=passed, failed=failed,
            awarded=awarded, message=message or "",
        )
    await db.commit()
    return {"passed": passed, "failed": failed, "credits": float(awarded)}


async def run_batch(db, batch_id) -> dict:
    """Phase 1 (verify) → Phase 2 (settle), recording the result on the batch."""
    batch = await db.get(VerificationBatch, batch_id)
    if batch is None:
        return {"error": "Batch not found"}
    if batch.status in (BatchStatus.COMPLETED.value, BatchStatus.FAILED.value):
        return {"status": batch.status, "already_processed": True}

    batch.status = BatchStatus.PROCESSING.value
    await db.commit()

    try:
        user = await db.get(User, batch.user_id)
        ids = [uuid.UUID(s) for s in (batch.engagement_ids or [])]

        engs = []
        if ids:
            engs = (
                await db.execute(
                    select(Engagement)
                    .where(
                        Engagement.id.in_(ids),
                        Engagement.user_id == user.id,
                        Engagement.verified.is_(False),
                        Engagement.credit_granted.is_(False),
                    )
                    .order_by(Engagement.clicked_at)
                )
            ).scalars().all()

        if not engs:
            return await _finish(
                db, batch, passed=0, failed=0, awarded=Decimal("0"),
                message="No pending engagements found",
            )

        post_ids = {e.post_id for e in engs}
        posts = {
            p.id: p
            for p in (
                await db.execute(select(Post).where(Post.id.in_(post_ids)))
            ).scalars().all()
        }
        items = []
        for e in engs:
            p = posts.get(e.post_id)
            tweet_id = ""
            if p:
                tweet_id = p.tweet_id or extract_tweet_id(p.x_link) or ""
            items.append(ToVerify(engagement_id=e.id, post_id=e.post_id, tweet_id=tweet_id))

        # Phase 1 — external, no locks. Pass db so verify_engagements can read
        # AUDIT_PROBABILITY (live, default 1.0 = always verify) for trusted-skip
        # sampling without re-hitting site_settings inline at the request layer.
        results = await verification.verify_engagements(
            items, user.x_username, db=db,
        )
        passed = sum(1 for r in results if r.passed)
        failed = sum(1 for r in results if not r.passed)

        # Phase 2 — atomic settlement
        s = await settlement.settle(db, user_id=user.id, results=results)
        awarded = s["total_awarded"]

        if failed == 0:
            message = f"Earned {float(awarded):.2f} karma for {passed} engagements!"
        else:
            message = (
                f"Earned {float(awarded):.2f} karma for {passed} engagements. "
                f"{failed} failed verification."
            )
        return await _finish(db, batch, passed=passed, failed=failed, awarded=awarded, message=message)

    except Exception as exc:  # mark failed; idempotency keys make a re-run safe
        logger.exception("[VERIFY] batch %s failed", batch_id)
        batch.status = BatchStatus.FAILED.value
        batch.message = str(exc)[:500]
        batch.completed_at = utcnow()
        await db.commit()
        return {"error": str(exc)}


async def process_batch_in_new_session(batch_id) -> dict:
    """BackgroundTasks entrypoint — runs run_batch in a fresh session.
    (Ch16 will replace the dispatch with an arq enqueue; this stays the body.)"""
    async with SessionLocal() as db:
        return await run_batch(db, batch_id)


# ---- cron: requeue stuck verification batches (Ch16, missing-cron audit) ----
# Parity with Django's `requeue_stuck_batches` management command. If an arq
# worker dies mid-batch, the VerificationBatch row stays in PROCESSING (or
# never leaves PENDING) and the user's pending engagements are stranded. This
# cron sweeps for those rows and re-enqueues them.
async def requeue_stuck_batches(
    db, *, older_than_minutes: int = 10, schedule=None,
) -> int:
    """Reset stuck pending/processing batches and re-enqueue them.

    A batch is "stuck" if it has been in pending or processing for longer than
    ``older_than_minutes`` (default 10 — well past the longest expected
    Phase 1 + Phase 2 round-trip). We flip status back to PENDING and re-fire
    the processor via ``schedule(batch_id)`` (the same shape ``queue_claim``
    uses). Returns the number of batches requeued.

    ``schedule=None`` makes the function safely callable from a test without
    a real arq pool — only the status reset runs.
    """
    from datetime import timedelta

    cutoff = utcnow() - timedelta(minutes=older_than_minutes)
    stuck = (
        await db.execute(
            select(VerificationBatch).where(
                VerificationBatch.status.in_((
                    BatchStatus.PENDING.value, BatchStatus.PROCESSING.value,
                )),
                VerificationBatch.created_at < cutoff,
            )
        )
    ).scalars().all()
    for batch in stuck:
        batch.status = BatchStatus.PENDING.value  # force back to pending
    await db.commit()
    if schedule is not None:
        for batch in stuck:
            await schedule(batch.id)
    if stuck:
        logger.warning(
            "requeue_stuck_batches: re-enqueued %s stuck batches", len(stuck)
        )
    return len(stuck)
