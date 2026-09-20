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
    def __init__(self, *, fail=False, error="telegram down", unconfigured=False, sink=None,
                 photo_fail=False):
        self.fail = fail
        self.error = error
        self.unconfigured = unconfigured
        self.sent = sink if sink is not None else []
        # sendPhoto is its own path: Telegram fetches the card URL itself and
        # 400s when it can't, while the text message would still go through
        self.photo_fail = photo_fail
        self.photos: list = []

    async def send_message(self, chat_id, text, parse_mode="HTML", reply_markup=None):
        if self.fail:
            raise RuntimeError(self.error)
        if self.unconfigured:
            return False  # the real client's "TELEGRAM_BOT_TOKEN not configured"
        self.sent.append((chat_id, text, reply_markup))
        return True

    async def send_photo(self, chat_id, photo_url, caption, parse_mode="HTML", reply_markup=None):
        if self.photo_fail:
            raise RuntimeError("Bad Request: wrong file identifier/HTTP URL specified")
        if self.fail:
            raise RuntimeError(self.error)
        if self.unconfigured:
            return False
        self.photos.append((chat_id, photo_url, caption, reply_markup))
        return True


def _mock_telegram(monkeypatch, **kw):
    monkeypatch.setattr(telegram, "get_telegram_client", lambda: _FakeTelegram(**kw))
    # outbox imported get_telegram_client by name — patch there too
    monkeypatch.setattr(outbox, "get_telegram_client", telegram.get_telegram_client)


async def _due_now(db_session, ev):
    """Skip the retry backoff: make the event due again."""
    ev.next_retry_at = utcnow() - timedelta(seconds=1)
    await db_session.commit()


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
    # backed off: not retried inside the same minute
    assert ev.next_retry_at > utcnow()
    assert (await outbox.drain(db_session))["processed"] == 0

    await _due_now(db_session, ev)
    await outbox.drain(db_session)
    await db_session.refresh(ev)
    await _due_now(db_session, ev)
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


async def test_retry_failed_skips_permanent_refusals_and_old_events(db_session, monkeypatch):
    blocked = OutboxEvent(
        event_type="telegram_notify", status="failed", payload={"telegram_id": 1},
        retry_count=3, error_message="Client error '403 Forbidden' for url 'https://api.telegram.org/…'",
    )
    rate_limited = OutboxEvent(
        event_type="telegram_notify", status="failed", payload={"telegram_id": 2},
        retry_count=3, error_message="Client error '429 Too Many Requests' for url '…'",
    )
    stale = OutboxEvent(
        event_type="telegram_notify", status="failed", payload={"telegram_id": 3},
        retry_count=3, error_message="timeout", created_at=utcnow() - timedelta(days=2),
    )
    db_session.add_all([blocked, rate_limited, stale])
    await db_session.commit()

    assert await outbox.retry_failed(db_session) == 1
    for ev in (blocked, rate_limited, stale):
        await db_session.refresh(ev)
    assert blocked.status == "failed"
    assert rate_limited.status == "pending" and rate_limited.retry_count == 2  # one more try
    assert stale.status == "failed"


async def test_rejection_reason_is_html_escaped(db_session, monkeypatch):
    """parse_mode=HTML: an unescaped "<" made Telegram 400 the message on
    every retry, so the rejection notice was silently lost."""
    sent: list = []
    _mock_telegram(monkeypatch, sink=sent)
    await OutboxService.queue_waitlist_rejected(
        db_session, entry_id=uuid.uuid4(), telegram_id=42,
        x_username="a<b>", reason="score < 100 & no <b>posts</b>",
    )
    await db_session.commit()
    assert (await outbox.drain(db_session))["sent"] == 1
    text = sent[0][1]
    assert "score &lt; 100 &amp; no &lt;b&gt;posts&lt;/b&gt;" in text
    assert "@a&lt;b&gt;" in text


async def test_send_skipped_without_bot_token_is_not_marked_sent(db_session, monkeypatch):
    _mock_telegram(monkeypatch, unconfigured=True)
    ev = await OutboxService.queue_telegram_notification(db_session, telegram_id=5, message="x")
    await db_session.commit()
    result = await outbox.drain(db_session)
    await db_session.refresh(ev)
    assert result["sent"] == 0 and ev.status == "pending"
    assert "TELEGRAM_BOT_TOKEN" in ev.error_message


async def test_cleanup_deletes_old_sent(db_session):
    old = OutboxEvent(
        event_type="telegram_notify", status="sent", payload={},
        created_at=utcnow() - timedelta(days=40),
    )
    db_session.add(old)
    await db_session.commit()
    n = await outbox.cleanup_old(db_session, older_than_days=30)
    assert n == 1


async def test_waitlist_register_queues_event(client, db_session, confirmed_x_proof):
    # registering through the API should leave a waitlist_submitted outbox row
    proof = await confirmed_x_proof(555, "someone", "42")
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
        reason="We couldn't verify that this account is yours",
    )
    await db_session.commit()
    assert ev.event_type == "x_verification_rejected"
    assert {"request_id", "telegram_id", "submitted_x_username",
            "claimed_x_username", "reason"} <= set(ev.payload)
    # the payload key is `reason` now, not `notes` — `notes` was fed the
    # reviewer's internal note and mailed it out
    assert "notes" not in ev.payload


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

    assert len(fake.photos) == 1        # the card image, not a bare message
    chat_id, _photo, _caption, reply_markup = fake.photos[0]
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
    monkeypatch.setattr(outbox.settings, "site_url", "")   # no origin -> no card either

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


# ============================================================================
# The public / internal boundary.
#
# Both review queues shipped a single free-text box labelled "internal —
# visible only in audit logs", handed that string to the outbox, and the
# template DM'd it to the applicant verbatim. The payload IS the draft
# message; these tests hold that line.
# ============================================================================


async def test_queue_refuses_internal_only_keys(db_session):
    """A payload key whose name says "admins only" is a bug, not a message."""
    import pytest

    for key in ("internal_note", "admin_notes", "private_note"):
        with pytest.raises(ValueError, match="internal-only"):
            await OutboxService.queue(
                db_session, "waitlist_rejected",
                {"telegram_id": 1, "reason": "not a fit", key: "obvious bot"},
            )


async def test_waitlist_rejection_carries_public_reason_only(db_session, monkeypatch):
    """The DM says what the applicant was told — and nothing the reviewer
    wrote for the team."""
    sent: list = []
    _mock_telegram(monkeypatch, sink=sent)
    ev = await OutboxService.queue_waitlist_rejected(
        db_session, entry_id=uuid.uuid4(), telegram_id=77,
        x_username="alice", reason="Not a fit for the beta right now",
    )
    await db_session.commit()
    assert "internal_note" not in ev.payload

    assert (await outbox.drain(db_session))["sent"] == 1
    text = sent[0][1]
    assert "Not a fit for the beta right now" in text


async def test_empty_reason_leaves_no_dangling_label(db_session, monkeypatch):
    """A seeded rejection has reason="" — the old template rendered
    "…was not approved at this time. Reason: " and stopped mid-sentence."""
    sent: list = []
    _mock_telegram(monkeypatch, sink=sent)
    await OutboxService.queue_waitlist_rejected(
        db_session, entry_id=uuid.uuid4(), telegram_id=78,
        x_username="bob", reason="",
    )
    await db_session.commit()
    assert (await outbox.drain(db_session))["sent"] == 1
    text = sent[0][1]
    assert "Reason" not in text
    assert text.endswith("at this time.")


async def test_empty_reason_strips_a_legacy_admin_template(db_session, monkeypatch):
    """Sites that already saved the pre-split template still say
    "Reason: {reason}". Rendering an empty reason through it must not leave
    the label stranded at the end of the message."""
    from app.models.site_setting import SiteSetting
    from app.services import site_settings

    db_session.add(SiteSetting(
        key="TG_MSG_WAITLIST_REJECTED", data_type="str",
        value="Your Loudrr waitlist application was not approved. Reason: {reason}",
    ))
    await db_session.commit()
    site_settings._cache.clear()

    sent: list = []
    _mock_telegram(monkeypatch, sink=sent)
    await OutboxService.queue_waitlist_rejected(
        db_session, entry_id=uuid.uuid4(), telegram_id=79, reason="",
    )
    await db_session.commit()
    assert (await outbox.drain(db_session))["sent"] == 1
    assert sent[0][1] == "Your Loudrr waitlist application was not approved."
    site_settings._cache.clear()


async def test_x_verification_rejection_renders_public_reason(db_session, monkeypatch):
    sent: list = []
    _mock_telegram(monkeypatch, sink=sent)
    await OutboxService.queue_x_verification_rejected(
        db_session, request_id=uuid.uuid4(), telegram_id=80,
        submitted_x_username="alice", claimed_x_username="alice_real",
        reason="We couldn't verify this account is yours",
    )
    await db_session.commit()
    assert (await outbox.drain(db_session))["sent"] == 1
    assert "Note: We couldn&#x27;t verify this account is yours" in sent[0][1]


async def test_x_verification_rejection_without_reason_has_no_note_label(
    db_session, monkeypatch,
):
    sent: list = []
    _mock_telegram(monkeypatch, sink=sent)
    await OutboxService.queue_x_verification_rejected(
        db_session, request_id=uuid.uuid4(), telegram_id=81,
        submitted_x_username="alice", claimed_x_username="alice_real", reason="",
    )
    await db_session.commit()
    assert (await outbox.drain(db_session))["sent"] == 1
    text = sent[0][1]
    assert "Note" not in text
    assert text.endswith("Claimed: @alice_real.")


async def test_legacy_notes_payload_still_renders(db_session, monkeypatch):
    """Events queued before the rename are sitting PENDING in prod with a
    `notes` key. They must keep delivering, not crash the drain."""
    sent: list = []
    _mock_telegram(monkeypatch, sink=sent)
    db_session.add(OutboxEvent(
        event_type="x_verification_rejected", status="pending",
        payload={
            "request_id": str(uuid.uuid4()), "telegram_id": 82,
            "submitted_x_username": "alice", "claimed_x_username": "alice_real",
            "notes": "queued before the split",
        },
    ))
    await db_session.commit()
    assert (await outbox.drain(db_session))["sent"] == 1
    assert "Note: queued before the split" in sent[0][1]


async def test_admin_ban_and_revoke_drop_an_empty_reason(db_session, monkeypatch):
    """Same dangling-label bug, same fix, for the other admin actions whose
    text reaches the person."""
    sent: list = []
    _mock_telegram(monkeypatch, sink=sent)
    await OutboxService.queue_admin_ban(
        db_session, user_id=uuid.uuid4(), telegram_id=83, reason="",
    )
    await OutboxService.queue_admin_revoke_credits(
        db_session, user_id=uuid.uuid4(), telegram_id=84, amount="5", reason="",
    )
    await OutboxService.queue_admin_grant_credits(
        db_session, user_id=uuid.uuid4(), telegram_id=85, amount="5", description="",
    )
    await db_session.commit()
    assert (await outbox.drain(db_session))["sent"] == 3
    ban, revoke, grant = (text for _, text, _ in sent)
    assert ban == "Your Loudrr account has been suspended."
    assert revoke == "5 karma was deducted from your balance."
    assert grant == "An admin granted you 5 karma."


async def test_admin_ban_keeps_a_real_reason(db_session, monkeypatch):
    sent: list = []
    _mock_telegram(monkeypatch, sink=sent)
    await OutboxService.queue_admin_ban(
        db_session, user_id=uuid.uuid4(), telegram_id=86, reason="ban evasion",
    )
    await db_session.commit()
    assert (await outbox.drain(db_session))["sent"] == 1
    assert sent[0][1] == "Your Loudrr account has been suspended. Reason: ban evasion"


async def test_public_text_is_clipped(db_session):
    """Telegram hard-fails past 4096 chars; the API caps `reason` at 300, and
    anything written straight to the outbox is clipped here."""
    ev = await OutboxService.queue_waitlist_rejected(
        db_session, entry_id=uuid.uuid4(), telegram_id=87, reason="x" * 5000,
    )
    await db_session.commit()
    assert len(ev.payload["reason"]) == outbox.MAX_PUBLIC_TEXT
    assert ev.payload["reason"].endswith("…")


# ============================================================================
# the waitlist cards ride along with the message (score + smart followers)
# ============================================================================
async def test_waitlist_card_is_sent_as_the_image_with_the_message_as_caption(
    db_session, monkeypatch,
):
    fake = _FakeTelegram()
    monkeypatch.setattr(telegram, "get_telegram_client", lambda: fake)
    monkeypatch.setattr(outbox, "get_telegram_client", telegram.get_telegram_client)
    monkeypatch.setattr(outbox.settings, "miniapp_url", "https://app.loudrr.com/app")

    await OutboxService.queue_waitlist_approved(
        db_session, entry_id=uuid.uuid4(), telegram_id=77, x_username="@Alice",
    )
    await db_session.commit()
    assert (await outbox.drain(db_session))["sent"] == 1

    assert fake.sent == []               # nothing went out as plain text
    chat_id, photo, caption, markup = fake.photos[0]
    assert chat_id == 77
    # the card route renders the stored Sorsa score + smart followers
    assert photo.startswith("https://app.loudrr.com/api/cards/waitlist?username=Alice&v=")
    assert "approved" in caption.lower() or "in!" in caption.lower()
    assert markup["inline_keyboard"][0][0]["text"] == "Open Loudrr"


async def test_waitlist_message_still_arrives_when_the_card_image_fails(
    db_session, monkeypatch,
):
    """Telegram 400s when it can't fetch the card URL. The applicant must still
    be told they're on the list."""
    fake = _FakeTelegram(photo_fail=True)
    monkeypatch.setattr(telegram, "get_telegram_client", lambda: fake)
    monkeypatch.setattr(outbox, "get_telegram_client", telegram.get_telegram_client)
    monkeypatch.setattr(outbox.settings, "miniapp_url", "https://app.loudrr.com/app")

    await OutboxService.queue_waitlist_submitted(
        db_session, entry_id=uuid.uuid4(), telegram_id=78, x_username="bob",
    )
    await db_session.commit()
    assert (await outbox.drain(db_session))["sent"] == 1
    assert fake.photos == []
    assert len(fake.sent) == 1 and fake.sent[0][0] == 78


async def test_no_card_without_a_frontend_origin(db_session, monkeypatch):
    fake = _FakeTelegram()
    monkeypatch.setattr(telegram, "get_telegram_client", lambda: fake)
    monkeypatch.setattr(outbox, "get_telegram_client", telegram.get_telegram_client)
    monkeypatch.setattr(outbox.settings, "miniapp_url", "")
    monkeypatch.setattr(outbox.settings, "site_url", "")

    await OutboxService.queue_waitlist_approved(
        db_session, entry_id=uuid.uuid4(), telegram_id=79, x_username="carol",
    )
    await db_session.commit()
    assert (await outbox.drain(db_session))["sent"] == 1
    assert fake.photos == [] and len(fake.sent) == 1


async def test_a_card_send_with_no_bot_token_is_not_marked_sent(db_session, monkeypatch):
    """`send_photo` returning False means nothing was delivered — the event has
    to stay retryable instead of being quietly marked sent."""
    fake = _FakeTelegram(unconfigured=True)
    monkeypatch.setattr(telegram, "get_telegram_client", lambda: fake)
    monkeypatch.setattr(outbox, "get_telegram_client", telegram.get_telegram_client)
    monkeypatch.setattr(outbox.settings, "miniapp_url", "https://app.loudrr.com/app")

    await OutboxService.queue_waitlist_approved(
        db_session, entry_id=uuid.uuid4(), telegram_id=80, x_username="dave",
    )
    await db_session.commit()
    result = await outbox.drain(db_session)
    assert result["sent"] == 0 and result["failed"] == 1
