"""The transactional outbox (Ch15, spec §5.5).

`queue()` inserts a pending event in the CALLER's transaction (no commit here)
— so the side-effect commits atomically with the business write, or not at all.
`drain()` is the worker body: claim pending events, dispatch them, mark sent or
(after max_retries) failed. Ch16 schedules drain/retry/cleanup; the logic lives
here and can be run by hand.
"""
import logging
from datetime import timedelta

from sqlalchemy import delete, select

from app.core.config import settings
from app.core.time_utils import utcnow
from app.integrations.telegram import get_telegram_client
from app.models.outbox_event import OutboxEvent, OutboxEventType, OutboxStatus
from app.repositories.outbox_event import OutboxEventRepository
from app.services.site_settings import get_setting

logger = logging.getLogger(__name__)


class OutboxService:
    """Create outbox events inside a business transaction. NO commit here."""

    @staticmethod
    async def queue(db, event_type: str, payload: dict) -> OutboxEvent:
        return await OutboxEventRepository(db).create(
            event_type=event_type, payload=payload, status=OutboxStatus.PENDING.value,
        )

    @staticmethod
    async def queue_telegram_notification(db, *, telegram_id, message, **extra) -> OutboxEvent:
        return await OutboxService.queue(
            db, OutboxEventType.TELEGRAM_NOTIFY.value,
            {"telegram_id": telegram_id, "message": message, **extra},
        )

    @staticmethod
    async def queue_waitlist_submitted(db, *, entry_id, telegram_id, x_username) -> OutboxEvent:
        # No email — Telegram-only signup (2026-08-07). Existing PENDING events
        # from before the removal may still have an "email" key in their JSON
        # payload; the dispatch handler ignores unknown keys, so they drain OK.
        return await OutboxService.queue(
            db, OutboxEventType.WAITLIST_SUBMITTED.value,
            {"entry_id": str(entry_id), "telegram_id": telegram_id,
             "x_username": x_username},
        )

    @staticmethod
    async def queue_waitlist_approved(db, *, entry_id, telegram_id, x_username) -> OutboxEvent:
        return await OutboxService.queue(
            db, OutboxEventType.WAITLIST_APPROVED.value,
            {"entry_id": str(entry_id), "telegram_id": telegram_id, "x_username": x_username},
        )

    @staticmethod
    async def queue_waitlist_rejected(
        db, *, entry_id, telegram_id, x_username="", reason="",
    ) -> OutboxEvent:
        return await OutboxService.queue(
            db, OutboxEventType.WAITLIST_REJECTED.value,
            {"entry_id": str(entry_id), "telegram_id": telegram_id,
             "x_username": x_username, "reason": reason or ""},
        )

    @staticmethod
    async def queue_x_verification_approved(
        db, *, request_id, user_id, telegram_id, x_username,
    ) -> OutboxEvent:
        return await OutboxService.queue(
            db, OutboxEventType.X_VERIFICATION_APPROVED.value,
            {"request_id": str(request_id), "user_id": str(user_id),
             "telegram_id": telegram_id, "x_username": x_username},
        )

    @staticmethod
    async def queue_x_verification_rejected(
        db, *, request_id, telegram_id, submitted_x_username="",
        claimed_x_username="", notes="",
    ) -> OutboxEvent:
        return await OutboxService.queue(
            db, OutboxEventType.X_VERIFICATION_REJECTED.value,
            {"request_id": str(request_id), "telegram_id": telegram_id,
             "submitted_x_username": submitted_x_username,
             "claimed_x_username": claimed_x_username, "notes": notes or ""},
        )

    @staticmethod
    async def queue_admin_grant_credits(
        db, *, user_id, telegram_id, amount, description="",
    ) -> OutboxEvent:
        return await OutboxService.queue(
            db, OutboxEventType.ADMIN_GRANT_CREDITS.value,
            {"user_id": str(user_id), "telegram_id": telegram_id,
             "amount": str(amount), "description": description or ""},
        )

    @staticmethod
    async def queue_admin_revoke_credits(
        db, *, user_id, telegram_id, amount, reason="",
    ) -> OutboxEvent:
        return await OutboxService.queue(
            db, OutboxEventType.ADMIN_REVOKE_CREDITS.value,
            {"user_id": str(user_id), "telegram_id": telegram_id,
             "amount": str(amount), "reason": reason or ""},
        )

    @staticmethod
    async def queue_admin_ban(
        db, *, user_id, telegram_id, reason="",
    ) -> OutboxEvent:
        return await OutboxService.queue(
            db, OutboxEventType.ADMIN_BAN.value,
            {"user_id": str(user_id), "telegram_id": telegram_id,
             "reason": reason or ""},
        )

    @staticmethod
    async def queue_daily_cap_reached(
        db, *, user_id, telegram_id, cap, daily_earned, date,
    ) -> OutboxEvent:
        return await OutboxService.queue(
            db, OutboxEventType.DAILY_CAP_REACHED.value,
            {"user_id": str(user_id), "telegram_id": telegram_id,
             "cap": str(cap), "daily_earned": str(daily_earned),
             "date": str(date)},
        )

    @staticmethod
    async def queue_claim_completed(
        db, *, batch_id, user_id, telegram_id, passed, failed, awarded, message="",
    ) -> OutboxEvent:
        return await OutboxService.queue(
            db, OutboxEventType.CLAIM_COMPLETED.value,
            {"batch_id": str(batch_id), "user_id": str(user_id),
             "telegram_id": telegram_id, "passed": int(passed),
             "failed": int(failed), "awarded": str(awarded),
             "message": message or ""},
        )

    @staticmethod
    async def queue_post_completed(
        db, *, post_id, user_id, telegram_id, total_engagements,
    ) -> OutboxEvent:
        return await OutboxService.queue(
            db, OutboxEventType.POST_COMPLETED.value,
            {"post_id": str(post_id), "user_id": str(user_id),
             "telegram_id": telegram_id,
             "total_engagements": int(total_engagements)},
        )

    @staticmethod
    async def queue_post_expired(
        db, *, post_id, user_id, telegram_id, refund_amount,
    ) -> OutboxEvent:
        return await OutboxService.queue(
            db, OutboxEventType.POST_EXPIRED.value,
            {"post_id": str(post_id), "user_id": str(user_id),
             "telegram_id": telegram_id, "refund_amount": str(refund_amount)},
        )

    @staticmethod
    async def queue_streak_milestone(
        db, *, user_id, telegram_id, streak, threshold, bonus,
    ) -> OutboxEvent | None:
        """Queue the 7/14/30-day milestone Telegram card.

        Idempotency key ``streak_milestone:<user>:<threshold>`` so re-running a
        batch (or a retried settle) cannot double-notify — returns the existing
        row (None to the caller) instead of raising on the unique violation.
        ``streak`` is the post-increment current_streak (==threshold today, but
        parameterized so a future "freeze-skip" feature can still send a card
        on the actual day).
        """
        idem = f"streak_milestone:{user_id}:{threshold}"
        existing = (
            await db.execute(
                select(OutboxEvent).where(
                    OutboxEvent.event_type == OutboxEventType.STREAK_MILESTONE.value,
                    OutboxEvent.idempotency_key == idem,
                ).limit(1)
            )
        ).scalar_one_or_none()
        if existing is not None:
            return None
        return await OutboxEventRepository(db).create(
            event_type=OutboxEventType.STREAK_MILESTONE.value,
            payload={
                "user_id": str(user_id), "telegram_id": telegram_id,
                "streak": int(streak), "threshold": int(threshold),
                "bonus": str(bonus),
            },
            status=OutboxStatus.PENDING.value,
            idempotency_key=idem,
        )


# ---- dispatch ----
# Hardcoded fallbacks used if the SiteSetting row is missing or the admin's
# template contains a typo that breaks .format(). These must match what the
# pre-refactor _waitlist_*_text helpers returned so existing behavior is
# preserved on a fresh DB.
_WAITLIST_SUBMITTED_DEFAULT = (
    "🎉 You're on the Loudrr waitlist{x_username_part}! "
    "We'll message you the moment you're approved."
)
_WAITLIST_APPROVED_DEFAULT = (
    "✅ You're in! Your Loudrr access is approved{x_username_part}. "
    "Open the app to start earning karma."
)
_WAITLIST_REJECTED_DEFAULT = (
    "Your Loudrr waitlist application{x_username_part} was not approved at this "
    "time. Reason: {reason}"
)
_X_VERIFICATION_APPROVED_DEFAULT = (
    "✅ Your X account{x_username_part} is verified. You can now earn karma on Loudrr."
)
_X_VERIFICATION_REJECTED_DEFAULT = (
    "Your X verification request was rejected. Submitted: @{submitted_x_username}, "
    "Claimed: @{claimed_x_username}. Note: {notes}"
)
_ADMIN_GRANT_CREDITS_DEFAULT = (
    "An admin granted you {amount} karma. {description}"
)
_ADMIN_REVOKE_CREDITS_DEFAULT = (
    "{amount} karma was deducted from your balance. Reason: {reason}"
)
_ADMIN_BAN_DEFAULT = (
    "Your Loudrr account has been suspended. Reason: {reason}"
)
_DAILY_CAP_REACHED_DEFAULT = (
    "You hit today's earning cap ({cap} karma). It resets at 00:00 UTC — "
    "see you tomorrow."
)
_CLAIM_COMPLETED_DEFAULT = (
    "Claim settled: earned {awarded} karma from {passed} engagements "
    "({failed} failed verification)."
)
_POST_COMPLETED_DEFAULT = (
    "Your post is complete — {total_engagements} engagements delivered. "
    "Escrow fully paid out."
)
_POST_EXPIRED_DEFAULT = (
    "Your post expired and {refund_amount} karma was refunded to your balance."
)
_STREAK_MILESTONE_DEFAULT = (
    "🔥 Day {streak} streak! +{bonus} bonus karma deposited."
)


# event_type → (SiteSetting key, hardcoded default). Drives _dispatch.
_TEMPLATE_BY_EVENT: dict[str, tuple[str, str]] = {
    OutboxEventType.WAITLIST_SUBMITTED.value: (
        "TG_MSG_WAITLIST_SUBMITTED", _WAITLIST_SUBMITTED_DEFAULT,
    ),
    OutboxEventType.WAITLIST_APPROVED.value: (
        "TG_MSG_WAITLIST_APPROVED", _WAITLIST_APPROVED_DEFAULT,
    ),
    OutboxEventType.WAITLIST_REJECTED.value: (
        "TG_MSG_WAITLIST_REJECTED", _WAITLIST_REJECTED_DEFAULT,
    ),
    OutboxEventType.X_VERIFICATION_APPROVED.value: (
        "TG_MSG_X_VERIFICATION_APPROVED", _X_VERIFICATION_APPROVED_DEFAULT,
    ),
    OutboxEventType.X_VERIFICATION_REJECTED.value: (
        "TG_MSG_X_VERIFICATION_REJECTED", _X_VERIFICATION_REJECTED_DEFAULT,
    ),
    OutboxEventType.ADMIN_GRANT_CREDITS.value: (
        "TG_MSG_ADMIN_GRANT_CREDITS", _ADMIN_GRANT_CREDITS_DEFAULT,
    ),
    OutboxEventType.ADMIN_REVOKE_CREDITS.value: (
        "TG_MSG_ADMIN_REVOKE_CREDITS", _ADMIN_REVOKE_CREDITS_DEFAULT,
    ),
    OutboxEventType.ADMIN_BAN.value: (
        "TG_MSG_ADMIN_BAN", _ADMIN_BAN_DEFAULT,
    ),
    OutboxEventType.DAILY_CAP_REACHED.value: (
        "TG_MSG_DAILY_CAP_REACHED", _DAILY_CAP_REACHED_DEFAULT,
    ),
    OutboxEventType.CLAIM_COMPLETED.value: (
        "TG_MSG_CLAIM_COMPLETED", _CLAIM_COMPLETED_DEFAULT,
    ),
    OutboxEventType.POST_COMPLETED.value: (
        "TG_MSG_POST_COMPLETED", _POST_COMPLETED_DEFAULT,
    ),
    OutboxEventType.POST_EXPIRED.value: (
        "TG_MSG_POST_EXPIRED", _POST_EXPIRED_DEFAULT,
    ),
    OutboxEventType.STREAK_MILESTONE.value: (
        "TG_MSG_STREAK_MILESTONE", _STREAK_MILESTONE_DEFAULT,
    ),
}

# Reserved event_types that intentionally do nothing (no user-facing send).
# Documented in OutboxEventType. Listed here so _dispatch can treat them as
# a clean no-op instead of failing the "unknown type" hardening check.
_NOOP_EVENTS = frozenset({
    OutboxEventType.CREDITS_EARNED.value,
    OutboxEventType.CAMPAIGN_WINNER.value,
    OutboxEventType.TWEETSCOUT_FETCH.value,
    OutboxEventType.EXTERNAL_API.value,
})


async def _render_template(db, key: str, payload: dict, default_template: str) -> str:
    """Render a Telegram message template stored in SiteSetting[key].

    Computes ``x_username_part`` from ``payload['x_username']`` (", @handle"
    or ""), fetches the template (falling back to ``default_template`` if the
    row is missing), and substitutes via ``str.format``. If the admin's
    template references an unknown placeholder we swallow the KeyError and
    re-render with the hardcoded default so a typo never crashes dispatch.
    """
    x_username_part = (
        f", @{payload['x_username']}" if payload.get("x_username") else ""
    )
    template = await get_setting(db, key, default=default_template)
    try:
        return template.format(x_username_part=x_username_part, **payload)
    except (KeyError, IndexError):
        return default_template.format(
            x_username_part=x_username_part, **payload
        )


# event_types that should ship with the "Open Loudrr" WebApp button (parity
# with the Django approval/waitlist Telegram cards). Other events render as
# bare text — they don't drive the user back to the mini-app.
_WEBAPP_BUTTON_EVENTS = frozenset({
    OutboxEventType.WAITLIST_APPROVED.value,
    OutboxEventType.WAITLIST_SUBMITTED.value,
})


def _waitlist_reply_markup(event_type: str) -> dict | None:
    """Return the inline_keyboard payload for a waitlist card, or None when
    the WebApp button isn't applicable / miniapp_url is unset."""
    if event_type not in _WEBAPP_BUTTON_EVENTS:
        return None
    if not settings.miniapp_url:
        return None
    return {
        "inline_keyboard": [
            [{"text": "Open Loudrr", "web_app": {"url": settings.miniapp_url}}]
        ]
    }


async def _dispatch(db, ev: OutboxEvent) -> None:
    """Deliver one event by type. Raises on failure (→ retry)."""
    p = ev.payload or {}
    telegram = get_telegram_client()

    if ev.event_type == OutboxEventType.TELEGRAM_NOTIFY.value:
        await telegram.send_message(p["telegram_id"], p.get("message", ""))
        return

    template = _TEMPLATE_BY_EVENT.get(ev.event_type)
    if template is not None:
        key, default = template
        if p.get("telegram_id"):
            text = await _render_template(db, key, p, default)
            # Parity with Django bots/telegram/notifications.py:43-46 — attach
            # an "Open Loudrr" WebApp button to the waitlist cards so the user
            # has a one-tap path back to the mini-app. Skipped (markup=None)
            # when settings.miniapp_url is empty so we don't send a broken btn.
            reply_markup = _waitlist_reply_markup(ev.event_type)
            await telegram.send_message(
                p["telegram_id"], text, reply_markup=reply_markup,
            )
        return

    if ev.event_type in _NOOP_EVENTS:
        # reserved enum values, no Telegram send wired today
        logger.info(
            "Outbox event %s (%s) — reserved/no-op, marking sent",
            ev.id, ev.event_type,
        )
        return

    # Unknown event_type — fail loudly so a missing dispatch branch can't masquerade
    # as a successful delivery in the metrics. Marks the event FAILED (after the
    # retry counter exhausts) with an actionable error_message.
    raise NotImplementedError(
        f"Unknown outbox event_type {ev.event_type!r}; "
        "register it in _TEMPLATE_BY_EVENT or _NOOP_EVENTS."
    )


async def drain(db, *, limit: int = 50) -> dict:
    """Claim up to `limit` pending events and deliver them."""
    rows = (
        await db.execute(
            select(OutboxEvent)
            .where(OutboxEvent.status == OutboxStatus.PENDING.value)
            .order_by(OutboxEvent.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    ).scalars().all()

    for ev in rows:
        ev.status = OutboxStatus.PROCESSING.value
    await db.flush()

    sent = failed = 0
    for ev in rows:
        try:
            await _dispatch(db, ev)
            ev.status = OutboxStatus.SENT.value
            ev.processed_at = utcnow()
            ev.error_message = ""
            sent += 1
        except Exception as e:  # noqa: BLE001 — any delivery failure → retry/fail
            ev.retry_count += 1
            ev.error_message = str(e)[:500]
            ev.status = (
                OutboxStatus.FAILED.value
                if ev.retry_count >= ev.max_retries
                else OutboxStatus.PENDING.value
            )
            failed += 1
        ev.updated_at = utcnow()

    await db.commit()
    return {"processed": len(rows), "sent": sent, "failed": failed}


async def retry_failed(db) -> int:
    """Reset failed events (still under max_retries) back to pending."""
    rows = (
        await db.execute(
            select(OutboxEvent).where(
                OutboxEvent.status == OutboxStatus.FAILED.value,
                OutboxEvent.retry_count < OutboxEvent.max_retries,
            )
        )
    ).scalars().all()
    for ev in rows:
        ev.status = OutboxStatus.PENDING.value
    await db.commit()
    return len(rows)


async def requeue_stuck_processing(db, *, older_than_minutes: int = 10) -> int:
    """Reset OutboxEvent rows stuck in `processing` back to `pending`.

    `drain()` flips PENDING → PROCESSING before delivery, then PROCESSING →
    SENT / PENDING / FAILED on outcome. If the worker crashes (OOM, SIGKILL,
    infra restart) between those two writes, the event stays PROCESSING
    forever and `drain()` never picks it up again — silent data-delivery
    loss (Telegram notifications, waitlist mails, X-verification pings).

    Mirrors the `requeue_stuck_batches` recovery pattern used for
    VerificationBatch. Runs on a cron so a crash's blast radius is bounded
    to `older_than_minutes` — well past the longest single dispatch we ever
    expect (Telegram sendMessage p99 ~2s). Returns the number of rows
    requeued so callers / logs can alert on non-zero counts.
    """
    cutoff = utcnow() - timedelta(minutes=older_than_minutes)
    stuck = (
        await db.execute(
            select(OutboxEvent).where(
                OutboxEvent.status == OutboxStatus.PROCESSING.value,
                OutboxEvent.updated_at < cutoff,
            )
        )
    ).scalars().all()
    for ev in stuck:
        ev.status = OutboxStatus.PENDING.value
        ev.updated_at = utcnow()
    await db.commit()
    if stuck:
        logger.warning(
            "outbox: requeued %d stuck-in-processing events (probable worker crash)",
            len(stuck),
        )
    return len(stuck)


async def cleanup_old(db, *, older_than_days: int = 30) -> int:
    """Delete sent events older than N days."""
    cutoff = utcnow() - timedelta(days=older_than_days)
    result = await db.execute(
        delete(OutboxEvent).where(
            OutboxEvent.status == OutboxStatus.SENT.value,
            OutboxEvent.created_at < cutoff,
        )
    )
    await db.commit()
    return result.rowcount or 0
