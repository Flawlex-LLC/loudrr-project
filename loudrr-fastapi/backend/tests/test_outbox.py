"""Ch15 — transactional outbox: queue, drain, retry, and waitlist wiring.
Telegram is mocked."""
import uuid
from datetime import timedelta


from app.core.time_utils import utcnow
from app.integrations import telegram
from app.models.outbox_event import OutboxEvent, OutboxStatus
from app.repositories.outbox_event import OutboxEventRepository
from app.services import outbox
from app.services.outbox import OutboxService


class _FakeTelegram:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.sent = []

    async def send_message(self, chat_id, text, parse_mode="HTML", reply_markup=None):
        if self.fail:
            raise RuntimeError("telegram down")
        self.sent.append((chat_id, text, reply_markup))
        return True


def _mock_telegram(monkeypatch, **kw):
    monkeypatch.setattr(telegram, "get_telegram_client", lambda: _FakeTelegram(**kw))
    # outbox imported get_telegram_client by name — patch there too
    monkeypatch.setattr(outbox, "get_telegram_client", telegram.get_telegram_client)


async def test_queue_creates_pending_event(db_session):
    ev = await OutboxService.queue_telegram_notification(
        db_session, telegram_id=123, message="hi"
    )
    await db_session.commit()
    assert ev.status == "pending"
    assert ev.payload["telegram_id"] == 123


async def test_drain_sends_and_marks_sent(db_session, monkeypatch):
    _mock_telegram(monkeypatch)
    await OutboxService.queue_telegram_notification(db_session, telegram_id=7, message="yo")
    await db_session.commit()

    result = await outbox.drain(db_session)
    assert result == {"processed": 1, "sent": 1, "failed": 0}

    ev = (await OutboxEventRepository(db_session).list(limit=1))[0]
    assert ev.status == "sent"
    assert ev.processed_at is not None


async def test_drain_retries_then_fails(db_session, monkeypatch):
    _mock_telegram(monkeypatch, fail=True)
    ev = await OutboxService.queue_telegram_notification(db_session, telegram_id=9, message="x")
    await db_session.commit()

    await outbox.drain(db_session)
    await db_session.refresh(ev)
    assert ev.status == "pending" and ev.retry_count == 1  # back to pending for retry

    await outbox.drain(db_session)
    await outbox.drain(db_session)
    await db_session.refresh(ev)
    assert ev.status == "failed" and ev.retry_count == 3  # exhausted max_retries


async def test_retry_failed_resets(db_session, monkeypatch):
    _mock_telegram(monkeypatch, fail=True)
    ev = await OutboxService.queue_telegram_notification(db_session, telegram_id=1, message="x")
    ev.max_retries = 5  # so it doesn't hit FAILED on a single drain
    await db_session.commit()
    await outbox.drain(db_session)
    await db_session.refresh(ev)
    # force it to failed to test the reset path
    ev.status = OutboxStatus.FAILED.value
    await db_session.commit()

    n = await outbox.retry_failed(db_session)
    assert n == 1
    await db_session.refresh(ev)
    assert ev.status == "pending"


async def test_cleanup_deletes_old_sent(db_session):
    old = OutboxEvent(
        event_type="telegram_notify", status="sent", payload={},
        created_at=utcnow() - timedelta(days=40),
    )
    db_session.add(old)
    await db_session.commit()
    n = await outbox.cleanup_old(db_session, older_than_days=30)
    assert n == 1


async def test_waitlist_register_queues_event(client, db_session):
    # registering through the API should leave a waitlist_submitted outbox row
    from app.core.crypto import sign_x_proof
    proof = sign_x_proof({
        "tg_id": 555, "x_username": "someone", "x_user_id": "42",
        "iat": int(utcnow().timestamp()),
    })
    r = await client.post(
        "/waitlist/register/",
        params={"telegram_id": 555},
        json={"x_proof": proof},
    )
    assert r.status_code == 200
    events = await OutboxEventRepository(db_session).list(limit=10)
    types = {e.event_type for e in events}
    assert "waitlist_submitted" in types


# ============================================================================
# Per-event queue tests — one per new event type added in the audit-and-wire
# pass. Each verifies the queue helper writes a pending OutboxEvent row with
# the expected event_type and payload keys. Keeps wiring honest: if anyone
# renames a key without updating these, the test breaks.
# ============================================================================


async def test_queue_waitlist_rejected(db_session):
    ev = await OutboxService.queue_waitlist_rejected(
        db_session, entry_id=uuid.uuid4(), telegram_id=101,
        x_username="alice", reason="off-niche",
    )
    await db_session.commit()
    assert ev.event_type == "waitlist_rejected"
    assert ev.status == "pending"
    assert {"entry_id", "telegram_id", "x_username", "reason"} <= set(ev.payload)
    assert ev.payload["reason"] == "off-niche"


async def test_queue_x_verification_approved(db_session):
    ev = await OutboxService.queue_x_verification_approved(
        db_session, request_id=uuid.uuid4(), user_id=uuid.uuid4(),
        telegram_id=202, x_username="0xBlest_",
    )
    await db_session.commit()
    assert ev.event_type == "x_verification_approved"
    assert {"request_id", "user_id", "telegram_id", "x_username"} <= set(ev.payload)


async def test_queue_x_verification_rejected(db_session):
    ev = await OutboxService.queue_x_verification_rejected(
        db_session, request_id=uuid.uuid4(), telegram_id=303,
        submitted_x_username="alice", claimed_x_username="alice_real",
        notes="handle doesn't match the screenshot",
    )
    await db_session.commit()
    assert ev.event_type == "x_verification_rejected"
    assert {"request_id", "telegram_id", "submitted_x_username",
            "claimed_x_username", "notes"} <= set(ev.payload)


async def test_queue_admin_grant_credits(db_session):
    ev = await OutboxService.queue_admin_grant_credits(
        db_session, user_id=uuid.uuid4(), telegram_id=404,
        amount="25", description="promo",
    )
    await db_session.commit()
    assert ev.event_type == "admin_grant_credits"
    assert {"user_id", "telegram_id", "amount", "description"} <= set(ev.payload)
    assert ev.payload["amount"] == "25"


async def test_queue_admin_revoke_credits(db_session):
    ev = await OutboxService.queue_admin_revoke_credits(
        db_session, user_id=uuid.uuid4(), telegram_id=505,
        amount="10", reason="spam",
    )
    await db_session.commit()
    assert ev.event_type == "admin_revoke_credits"
    assert {"user_id", "telegram_id", "amount", "reason"} <= set(ev.payload)


async def test_queue_admin_ban(db_session):
    ev = await OutboxService.queue_admin_ban(
        db_session, user_id=uuid.uuid4(), telegram_id=606, reason="bot",
    )
    await db_session.commit()
    assert ev.event_type == "admin_ban"
    assert {"user_id", "telegram_id", "reason"} <= set(ev.payload)
    assert ev.payload["reason"] == "bot"


async def test_queue_daily_cap_reached(db_session):
    ev = await OutboxService.queue_daily_cap_reached(
        db_session, user_id=uuid.uuid4(), telegram_id=707,
        cap=160, daily_earned=160, date="2026-06-06",
    )
    await db_session.commit()
    assert ev.event_type == "daily_cap_reached"
    assert {"user_id", "telegram_id", "cap", "daily_earned", "date"} <= set(ev.payload)


async def test_queue_claim_completed(db_session):
    ev = await OutboxService.queue_claim_completed(
        db_session, batch_id=uuid.uuid4(), user_id=uuid.uuid4(),
        telegram_id=808, passed=8, failed=2, awarded="8.0000",
    )
    await db_session.commit()
    assert ev.event_type == "claim_completed"
    assert {"batch_id", "user_id", "telegram_id", "passed", "failed",
            "awarded"} <= set(ev.payload)
    assert ev.payload["passed"] == 8
    assert ev.payload["failed"] == 2


async def test_queue_post_completed(db_session):
    ev = await OutboxService.queue_post_completed(
        db_session, post_id=uuid.uuid4(), user_id=uuid.uuid4(),
        telegram_id=909, total_engagements=42,
    )
    await db_session.commit()
    assert ev.event_type == "post_completed"
    assert {"post_id", "user_id", "telegram_id", "total_engagements"} <= set(ev.payload)
    assert ev.payload["total_engagements"] == 42


async def test_queue_post_expired(db_session):
    ev = await OutboxService.queue_post_expired(
        db_session, post_id=uuid.uuid4(), user_id=uuid.uuid4(),
        telegram_id=1010, refund_amount="15.0000",
    )
    await db_session.commit()
    assert ev.event_type == "post_expired"
    assert {"post_id", "user_id", "telegram_id", "refund_amount"} <= set(ev.payload)


# ============================================================================
# Dispatch round-trip for claim_completed — the single highest-impact new
# event. Proves the full path: queue -> drain -> template render -> Telegram
# send. The pattern works for every event type (one branch in _dispatch); this
# test exercises the new template renderer + dispatch wiring once for the most
# valuable user-facing notification.
# ============================================================================
# ============================================================================
# Send-shape parity (P1) — waitlist_approved and waitlist_submitted ship with
# an "Open Loudrr" WebApp inline-keyboard button so users have a one-tap path
# back to the mini-app (parity with Django bots/telegram/notifications.py).
# Other event types (e.g. claim_completed) deliver bare text — no keyboard.
# ============================================================================
async def test_drain_attaches_webapp_button_for_waitlist_approved(
    db_session, monkeypatch
):
    """Parity with Django: WAITLIST_APPROVED dispatch should attach the
    inline-keyboard with the WebApp button when settings.miniapp_url is set."""
    fake = _FakeTelegram()
    monkeypatch.setattr(telegram, "get_telegram_client", lambda: fake)
    monkeypatch.setattr(outbox, "get_telegram_client", telegram.get_telegram_client)
    monkeypatch.setattr(outbox.settings, "miniapp_url", "https://miniapp.example/")

    await OutboxService.queue_waitlist_approved(
        db_session, entry_id=uuid.uuid4(), telegram_id=42, x_username="alice",
    )
    await db_session.commit()
    result = await outbox.drain(db_session)
    assert result["sent"] == 1 and result["failed"] == 0

    assert len(fake.sent) == 1
    chat_id, _text, reply_markup = fake.sent[0]
    assert chat_id == 42
    assert reply_markup is not None
    # one row, one button: "Open Loudrr" with a WebApp link
    btn = reply_markup["inline_keyboard"][0][0]
    assert btn["text"] == "Open Loudrr"
    assert btn["web_app"]["url"] == "https://miniapp.example/"


async def test_drain_no_webapp_button_when_miniapp_url_unset(
    db_session, monkeypatch
):
    """If miniapp_url is empty (dev/test default), dispatch must NOT send a
    half-broken inline keyboard — falls back to bare text."""
    fake = _FakeTelegram()
    monkeypatch.setattr(telegram, "get_telegram_client", lambda: fake)
    monkeypatch.setattr(outbox, "get_telegram_client", telegram.get_telegram_client)
    monkeypatch.setattr(outbox.settings, "miniapp_url", "")

    await OutboxService.queue_waitlist_submitted(
        db_session, entry_id=uuid.uuid4(), telegram_id=43,
        x_username="bob",
    )
    await db_session.commit()
    await outbox.drain(db_session)

    assert len(fake.sent) == 1
    _chat_id, _text, reply_markup = fake.sent[0]
    assert reply_markup is None  # no half-baked button when URL is unset


async def test_drain_no_webapp_button_for_non_waitlist_events(
    db_session, monkeypatch
):
    """The WebApp button is waitlist-card-only: claim_completed et al render
    as plain text even when miniapp_url is configured."""
    fake = _FakeTelegram()
    monkeypatch.setattr(telegram, "get_telegram_client", lambda: fake)
    monkeypatch.setattr(outbox, "get_telegram_client", telegram.get_telegram_client)
    monkeypatch.setattr(outbox.settings, "miniapp_url", "https://miniapp.example/")

    await OutboxService.queue_claim_completed(
        db_session, batch_id=uuid.uuid4(), user_id=uuid.uuid4(),
        telegram_id=44, passed=3, failed=0, awarded="3.0000",
    )
    await db_session.commit()
    await outbox.drain(db_session)

    assert len(fake.sent) == 1
    _chat_id, _text, reply_markup = fake.sent[0]
    assert reply_markup is None


async def test_drain_dispatches_claim_completed(db_session, monkeypatch):
    _mock_telegram(monkeypatch)
    await OutboxService.queue_claim_completed(
        db_session, batch_id=uuid.uuid4(), user_id=uuid.uuid4(),
        telegram_id=12345, passed=8, failed=2, awarded="8.0000",
    )
    await db_session.commit()

    result = await outbox.drain(db_session)
    assert result["sent"] == 1 and result["failed"] == 0

    # The mock factory returns a fresh instance per call, so we can't read
    # .sent back from the client. Instead, verify the OutboxEvent is now
    # sent + processed_at set — proves the dispatch path executed.
    ev = (await OutboxEventRepository(db_session).list(limit=1))[0]
    assert ev.status == "sent"
    assert ev.processed_at is not None


async def test_requeue_stuck_processing_recovers_crashed_worker(db_session):
    """A worker that dies AFTER flipping PENDING→PROCESSING but BEFORE writing
    SENT/PENDING/FAILED strands the event in PROCESSING forever — drain()
    skips it because its filter is `status=PENDING`. The sweeper flips it
    back so the next drain picks it up."""
    from datetime import timedelta

    # Fresh event, then simulate a mid-dispatch worker crash: mark it
    # PROCESSING, set updated_at BEFORE the sweeper cutoff (default 10 min).
    ev = await OutboxService.queue_telegram_notification(
        db_session, telegram_id=999, message="post-crash orphan"
    )
    ev.status = "processing"
    ev.updated_at = utcnow() - timedelta(minutes=15)  # older than 10-min cutoff
    await db_session.commit()

    requeued = await outbox.requeue_stuck_processing(db_session)
    assert requeued == 1

    await db_session.refresh(ev)
    assert ev.status == "pending"


async def test_requeue_stuck_processing_leaves_fresh_processing_alone(db_session):
    """A worker mid-dispatch (still within the cutoff) must NOT be preempted —
    that would race the real worker's SENT write and duplicate delivery."""
    from datetime import timedelta

    ev = await OutboxService.queue_telegram_notification(
        db_session, telegram_id=1000, message="actively being sent"
    )
    ev.status = "processing"
    ev.updated_at = utcnow() - timedelta(seconds=30)  # well within the 10-min cutoff
    await db_session.commit()

    requeued = await outbox.requeue_stuck_processing(db_session)
    assert requeued == 0

    await db_session.refresh(ev)
    assert ev.status == "processing"  # untouched


async def test_worker_registers_stuck_outbox_sweeper():
    """The arq worker MUST include the stuck-outbox sweeper — a wiring test
    so the fn doesn't silently drop out of the schedule."""
    from app.tasks.worker import WorkerSettings
    fn_names = {f.__name__ for f in WorkerSettings.functions}
    assert "requeue_stuck_outbox_events" in fn_names
