"""Service-level tests for the waitlist use cases (register/status/approve/reject).
These exercise the service + both repositories against the real DB."""
import uuid

import pytest
from sqlalchemy import select

from app.core.crypto import sign_x_proof
from app.core.errors import BadRequest, Conflict, NotFound
from app.core.time_utils import utcnow
from app.models.audit_log import AuditLog
from app.models.outbox_event import OutboxEvent
from app.models.waitlist_entry import WaitlistEntry
from app.schemas.waitlist import WaitlistRegisterRequest
from app.services import waitlist as svc


def _proof(tg_id: int = 111, username: str = "alice", x_user_id: str = "1"):
    return sign_x_proof({
        "tg_id": tg_id,
        "x_username": username,
        "x_user_id": x_user_id,
        "iat": int(utcnow().timestamp()),
    })


def _payload(*, tg_id=111, username="alice", x_user_id="1", **kw):
    """A signed proof with NO confirmed handoff row: fine wherever register
    must answer before looking at the proof (idempotency, tg_id mismatch)."""
    return WaitlistRegisterRequest(
        x_proof=_proof(tg_id=tg_id, username=username, x_user_id=x_user_id),
        **kw,
    )


@pytest.fixture
def confirmed_payload(confirmed_x_proof):
    """``await confirmed_payload(tg_id=…)`` -> a register body whose proof the
    X account owner has confirmed in the browser (what register requires)."""
    async def _make(*, tg_id=111, username="alice", x_user_id="1", **kw):
        return WaitlistRegisterRequest(
            x_proof=await confirmed_x_proof(tg_id, username, x_user_id), **kw,
        )
    return _make


def _tg(id=111, username="alice", first_name="Alice"):
    return {"id": id, "username": username, "first_name": first_name}


async def test_register_creates_entry(db_session, confirmed_payload):
    result = await svc.register_entry(db_session, tg_user=_tg(), payload=await confirmed_payload())
    assert result.was_new is True
    assert result.entry.x_username == "alice"
    assert result.entry.status == "submitted"
    assert result.entry.x_verified is True     # OAuth-verified at registration
    assert result.entry.x_user_id == "1"        # from the proof payload
    assert result.entry.referral_code           # a code was generated


async def test_register_idempotent_on_telegram_id(db_session, confirmed_payload):
    first = await svc.register_entry(db_session, tg_user=_tg(), payload=await confirmed_payload())
    # same telegram id, different x handle proof -> still treated as the same user
    second = await svc.register_entry(
        db_session, tg_user=_tg(),
        payload=_payload(username="bob", x_user_id="2"),
    )
    assert second.was_new is False
    assert second.entry.id == first.entry.id


async def test_register_rejects_invalid_proof(db_session):
    from app.schemas.waitlist import WaitlistRegisterRequest
    # 40 chars clears OAuthProof's min_length=32 shape gate; the bad
    # signature is then rejected by verify_x_proof -> BadRequest.
    payload = WaitlistRegisterRequest(x_proof="clearly-not-a-real-token-padded-to-40-xx")
    with pytest.raises(BadRequest):
        await svc.register_entry(db_session, tg_user=_tg(), payload=payload)


async def test_register_rejects_proof_bound_to_other_tg_id(db_session):
    # proof signed with tg_id=1, request auth as tg_id=2 -> reject
    with pytest.raises(BadRequest) as exc:
        await svc.register_entry(
            db_session, tg_user=_tg(id=2, username="bob"),
            payload=_payload(tg_id=1, username="alice", x_user_id="1"),
        )
    assert "different Telegram user" in str(exc.value)


async def test_register_rejects_duplicate_x_username(db_session, confirmed_payload):
    """Two different Telegram accounts trying to claim the same X handle
    must conflict — one X account = one waitlist entry."""
    await svc.register_entry(db_session, tg_user=_tg(id=1), payload=await confirmed_payload(tg_id=1))
    with pytest.raises(BadRequest, match="X username already registered"):
        await svc.register_entry(
            db_session, tg_user=_tg(id=2, username="bob"),
            # same X handle, different tg id — proof still valid for tg=2
            payload=await confirmed_payload(tg_id=2, username="alice", x_user_id="1"),
        )


async def test_status_lifecycle(db_session, confirmed_payload):
    assert (await svc.get_status(db_session, telegram_id=999)).status == "not_registered"
    await svc.register_entry(
        db_session, tg_user=_tg(id=999), payload=await confirmed_payload(tg_id=999),
    )
    assert (await svc.get_status(db_session, telegram_id=999)).status == "waitlisted"


async def test_approve_creates_user(db_session, make_user, confirmed_payload):
    admin = await make_user()  # approved_by_id is a real FK -> users.id
    reg = await svc.register_entry(db_session, tg_user=_tg(), payload=await confirmed_payload())
    user = await svc.approve_entry(
        db_session, entry_id=reg.entry.id, admin_id=admin.id
    )
    assert user.x_username == "alice"
    await db_session.refresh(reg.entry)
    assert reg.entry.status == "approved"
    assert reg.entry.created_user_id == user.id
    # the user now exists -> status flips to approved
    assert (await svc.get_status(db_session, telegram_id=111)).status == "approved"
    # launch audit (2026-09): approval carries the OAuth result over. Without
    # this the mini-app skipped onboarding (is_whitelisted False) and the
    # OAuth-proven id never reached x_profiles, so submit_post refused everyone.
    assert user.is_whitelisted is True
    assert user.x_verified is True
    assert user.x_verified_at is not None
    from app.repositories.x_profile import XProfileRepository
    profile = await XProfileRepository(db_session).get(user_id=user.id)
    assert profile is not None
    assert profile.x_user_id == "1"
    assert profile.username == "alice"


async def test_approve_twice_conflicts(db_session, make_user, confirmed_payload):
    admin = await make_user()
    reg = await svc.register_entry(db_session, tg_user=_tg(), payload=await confirmed_payload())
    await svc.approve_entry(db_session, entry_id=reg.entry.id, admin_id=admin.id)
    with pytest.raises(Conflict):
        await svc.approve_entry(db_session, entry_id=reg.entry.id, admin_id=admin.id)


async def test_reject_sets_status(db_session, make_user, confirmed_payload):
    admin = await make_user()
    reg = await svc.register_entry(db_session, tg_user=_tg(), payload=await confirmed_payload())
    entry = await svc.reject_entry(
        db_session, entry_id=reg.entry.id, admin_id=admin.id, reason="spam"
    )
    assert entry.status == "rejected"
    assert entry.rejection_reason == "spam"


# ---- scores: fetched at sign-up (queued) — approval copies, never fetches ----
def _capture_enqueues(monkeypatch) -> list:
    captured: list[tuple[str, tuple, dict]] = []

    async def _fake_enqueue(task_name, *args, **kwargs):
        captured.append((task_name, args, kwargs))
        return True

    monkeypatch.setattr("app.tasks.enqueue.enqueue", _fake_enqueue)
    return captured


async def test_register_queues_the_signup_score_fetch(db_session, monkeypatch, confirmed_payload):
    captured = _capture_enqueues(monkeypatch)
    reg = await svc.register_entry(db_session, tg_user=_tg(), payload=await confirmed_payload())
    assert captured == [
        ("fetch_waitlist_score", (str(reg.entry.id),), {"job_id": f"score:{reg.entry.id}"}),
    ]
    # registering again is idempotent — no second fetch
    await svc.register_entry(db_session, tg_user=_tg(), payload=_payload())
    assert len(captured) == 1


async def test_approve_copies_the_signup_score_without_fetching(db_session, make_user, monkeypatch, confirmed_payload):
    admin = await make_user()
    reg = await svc.register_entry(db_session, tg_user=_tg(), payload=await confirmed_payload())
    fetched_at = utcnow()
    reg.entry.score = 587.2
    reg.entry.score_updated_at = fetched_at
    reg.entry.score_data = {
        "id": "999", "screen_name": "alice", "name": "Alice", "score": 587.2,
        "followers_count": 9251, "smart_followers": 618,
        "top_followers": [{"username": "notthreadguy"}],
    }
    await db_session.commit()

    captured = _capture_enqueues(monkeypatch)
    user = await svc.approve_entry(db_session, entry_id=reg.entry.id, admin_id=admin.id)

    assert captured == []  # no provider fetch at approval
    assert user.tweetscout_score == 587.2
    assert user.tweetscout_last_updated == fetched_at
    from app.repositories.x_profile import XProfileRepository
    profile = await XProfileRepository(db_session).get(user_id=user.id)
    assert profile.followers_count == 9251
    assert profile.raw_tweetscout_data["smart_followers"] == 618
    assert profile.x_user_id == "1"  # the OAuth-proven id beats the provider's "999"


async def test_approve_without_signup_score_leaves_it_to_onboarding(db_session, make_user, monkeypatch, confirmed_payload):
    admin = await make_user()
    reg = await svc.register_entry(db_session, tg_user=_tg(), payload=await confirmed_payload())
    captured = _capture_enqueues(monkeypatch)
    user = await svc.approve_entry(db_session, entry_id=reg.entry.id, admin_id=admin.id)
    assert captured == []
    # unscored + never fetched -> the mini-app shows onboarding, which fetches
    assert user.tweetscout_score == 0
    assert user.tweetscout_last_updated is None

# ============================================================================
# THE REVIEW QUEUE — what the admin panel needs from this service.
#
# Covers the three things the panel could not do before: tell the applicant
# why without leaking the reviewer's note, undo a rejection, and ask for a
# score that never arrived. Plus the list contract the table is built on.
# ============================================================================
async def _entry(db, **kw):
    """Insert a WaitlistEntry directly — the list tests need shapes that the
    register flow (one X account, one Telegram id, always 'submitted') can't
    produce."""
    values = dict(
        telegram_id=uuid.uuid4().int % 1_000_000_000,
        telegram_username="",
        telegram_display_name="",
        x_username=f"u{uuid.uuid4().hex[:8]}",
        referral_code=uuid.uuid4().hex[:10].upper(),
        status="submitted",
    )
    values.update(kw)
    row = WaitlistEntry(**values)
    db.add(row)
    await db.commit()
    return row


async def _audit_detail(db, action: str) -> dict:
    log = (
        await db.execute(select(AuditLog).where(AuditLog.action == action))
    ).scalar_one()
    return log.detail


async def _outbox_payloads(db, event_type: str) -> list[dict]:
    rows = (
        await db.execute(select(OutboxEvent).where(OutboxEvent.event_type == event_type))
    ).scalars().all()
    return [r.payload for r in rows]


# ---- 1. the public reason / internal note split ----
async def test_reject_sends_public_reason_and_never_the_internal_note(
    db_session, make_user, confirmed_payload,
):
    """THE bug: both admin pages called the field "internal — visible only in
    audit logs" and the service DM'd it to the applicant verbatim."""
    admin = await make_user(telegram_id=999)
    reg = await svc.register_entry(db_session, tg_user=_tg(), payload=await confirmed_payload())

    await svc.reject_entry(
        db_session, entry_id=reg.entry.id, admin_id=admin.id,
        reason="Not a fit for the beta right now",
        internal_note="obvious bot farm, 4-day-old account",
    )

    payloads = await _outbox_payloads(db_session, "waitlist_rejected")
    assert len(payloads) == 1
    assert payloads[0]["reason"] == "Not a fit for the beta right now"
    # the whole point: nothing the reviewer wrote for the team is in the
    # message the applicant receives, under any key
    assert "obvious bot farm" not in str(payloads[0])
    assert "internal_note" not in payloads[0]

    detail = await _audit_detail(db_session, "reject_waitlist")
    assert detail["reason"] == "Not a fit for the beta right now"
    assert detail["internal_note"] == "obvious bot farm, 4-day-old account"


async def test_reject_with_only_an_internal_note_sends_no_reason(
    db_session, make_user, confirmed_payload,
):
    """A reviewer who only fills in the internal box has said nothing public —
    the DM must carry an empty reason, not the note."""
    admin = await make_user(telegram_id=998)
    reg = await svc.register_entry(db_session, tg_user=_tg(), payload=await confirmed_payload())

    entry = await svc.reject_entry(
        db_session, entry_id=reg.entry.id, admin_id=admin.id,
        internal_note="dupe of @seed_tomas",
    )
    assert entry.rejection_reason == ""
    payloads = await _outbox_payloads(db_session, "waitlist_rejected")
    assert payloads[0]["reason"] == ""
    assert "dupe of" not in str(payloads[0])


# ---- 2. reopen: the undo ----
async def test_reopen_returns_a_rejected_entry_to_the_queue(db_session, make_user):
    admin = await make_user(telegram_id=997)
    entry = await _entry(
        db_session, status="rejected", rejection_reason="AUDIT TEST: low-signal account",
        approved_at=utcnow(), approved_by_id=admin.id, x_verified=True,
    )

    reopened = await svc.reopen_entry(db_session, entry_id=entry.id, admin_id=admin.id)

    assert reopened.status == "submitted"
    assert reopened.rejection_reason == ""
    assert reopened.approved_at is None
    assert reopened.approved_by_id is None
    # the column Ch10 added for this and nothing ever wrote: they already
    # passed X OAuth, so re-approval must not make them do it again
    assert reopened.x_verified_previously is True


async def test_reopen_is_audited_with_the_reason_it_cleared(db_session, make_user):
    admin = await make_user(telegram_id=996)
    entry = await _entry(
        db_session, status="rejected", rejection_reason="mis-clicked",
        x_username="carol",
    )
    await svc.reopen_entry(
        db_session, entry_id=entry.id, admin_id=admin.id,
        internal_note="wrong row, meant the one below",
    )
    detail = await _audit_detail(db_session, "reopen_waitlist")
    assert detail["cleared_reason"] == "mis-clicked"
    assert detail["internal_note"] == "wrong row, meant the one below"
    assert detail["x_username"] == "carol"


async def test_reopen_sends_no_telegram_message(db_session, make_user):
    admin = await make_user(telegram_id=9959)
    entry = await _entry(db_session, status="rejected")
    await svc.reopen_entry(db_session, entry_id=entry.id, admin_id=admin.id)
    events = (await db_session.execute(select(OutboxEvent))).scalars().all()
    assert events == []


async def test_reopen_refuses_anything_but_rejected(db_session, make_user):
    admin = await make_user(telegram_id=995)
    submitted = await _entry(db_session)
    approved = await _entry(db_session, status="approved")

    for entry in (submitted, approved):
        with pytest.raises(Conflict):
            await svc.reopen_entry(db_session, entry_id=entry.id, admin_id=admin.id)


async def test_reopen_refuses_when_a_user_was_already_created(db_session, make_user):
    """A rejected row that still points at a User (legacy data, a hand-edit)
    would let approve_entry try to create a second User on the same
    telegram_id and blow up on the unique index."""
    admin = await make_user(telegram_id=994)
    ghost = await make_user(telegram_id=993)
    entry = await _entry(db_session, status="rejected", created_user_id=ghost.id)

    with pytest.raises(Conflict, match="already created a user"):
        await svc.reopen_entry(db_session, entry_id=entry.id, admin_id=admin.id)


async def test_reopen_missing_entry_404s(db_session, make_user):
    admin = await make_user(telegram_id=992)
    with pytest.raises(NotFound):
        await svc.reopen_entry(db_session, entry_id=uuid.uuid4(), admin_id=admin.id)


async def test_reopened_entry_can_then_be_approved(db_session, make_user, confirmed_payload):
    """The full recovery path: reject by mistake, reopen, approve."""
    admin = await make_user(telegram_id=991)
    reg = await svc.register_entry(db_session, tg_user=_tg(), payload=await confirmed_payload())
    await svc.reject_entry(
        db_session, entry_id=reg.entry.id, admin_id=admin.id, reason="oops",
    )
    await svc.reopen_entry(db_session, entry_id=reg.entry.id, admin_id=admin.id)

    user = await svc.approve_entry(db_session, entry_id=reg.entry.id, admin_id=admin.id)
    assert user.is_whitelisted is True
    await db_session.refresh(reg.entry)
    assert reg.entry.status == "approved"


# ---- 3. refresh-score ----
async def test_admin_refresh_score_runs_inline_without_a_queue(
    db_session, make_user, monkeypatch,
):
    """No arq configured (tests, local dev) -> run the same job in-process and
    report its outcome instead of silently doing nothing."""
    from app.services import scores as scores_svc

    admin = await make_user(telegram_id=990)
    # "no score": a finished fetch that found nothing — the state the old
    # panel had no answer for
    entry = await _entry(db_session, x_username="dora", score_updated_at=utcnow())

    async def _fake_fetch(db, entry_id):
        row = await db.get(WaitlistEntry, uuid.UUID(str(entry_id)))
        row.score = 512.0
        row.score_updated_at = utcnow()
        await db.commit()
        return "found"

    monkeypatch.setattr(scores_svc, "fetch_waitlist_score", _fake_fetch)

    out = await svc.admin_refresh_score(db_session, entry_id=entry.id, admin_id=admin.id)
    assert out["queued"] is False
    assert out["result"] == "found"
    assert out["entry"]["score"] == 512.0


async def test_admin_refresh_score_clears_the_stamp_that_blocks_a_refetch(
    db_session, make_user, monkeypatch,
):
    """fetch_waitlist_score returns "skipped" when score_updated_at is set —
    that guard is for the sign-up job racing the user's own Refresh, and it
    made an admin refresh a no-op."""
    from app.services import scores as scores_svc

    admin = await make_user(telegram_id=989)
    entry = await _entry(db_session, x_username="eve", score_updated_at=utcnow())

    seen: list = []

    async def _fake_fetch(db, entry_id):
        row = await db.get(WaitlistEntry, uuid.UUID(str(entry_id)))
        seen.append(row.score_updated_at)
        return "not_found"

    monkeypatch.setattr(scores_svc, "fetch_waitlist_score", _fake_fetch)
    await svc.admin_refresh_score(db_session, entry_id=entry.id, admin_id=admin.id)
    assert seen == [None]


async def test_admin_refresh_score_prefers_the_queue(db_session, make_user, monkeypatch):
    admin = await make_user(telegram_id=988)
    entry = await _entry(db_session, x_username="frank")
    captured = _capture_enqueues(monkeypatch)

    out = await svc.admin_refresh_score(db_session, entry_id=entry.id, admin_id=admin.id)
    assert out["queued"] is True and out["result"] is None
    # same job, same idempotency key as the sign-up fetch — a queued sign-up
    # job and an impatient admin can't run the same entry twice
    assert captured == [
        ("fetch_waitlist_score", (str(entry.id),), {"job_id": f"score:{entry.id}"}),
    ]
    assert (await _audit_detail(db_session, "refresh_waitlist_score"))["x_username"] == "frank"


async def test_admin_refresh_score_needs_a_handle(db_session, make_user):
    admin = await make_user(telegram_id=987)
    entry = await _entry(db_session, x_username="")
    with pytest.raises(BadRequest):
        await svc.admin_refresh_score(db_session, entry_id=entry.id, admin_id=admin.id)


# ---- 4. the list: filters, sort, paging, totals, enrichment ----
async def test_list_returns_total_independent_of_the_page(db_session):
    for _ in range(7):
        await _entry(db_session)

    page = await svc.list_entries(db_session, limit=3)
    assert len(page["items"]) == 3
    # the old endpoint returned a bare list capped at 200: applicant 201 was
    # simply absent, with nothing on screen to say so
    assert page["total"] == 7
    assert page["limit"] == 3 and page["offset"] == 0

    second = await svc.list_entries(db_session, limit=3, offset=3)
    assert second["total"] == 7
    assert {r["id"] for r in second["items"]} & {r["id"] for r in page["items"]} == set()


async def test_list_filters_by_status_so_history_is_reachable(db_session, make_user):
    admin = await make_user(telegram_id=986)
    await _entry(db_session)
    await _entry(db_session, status="approved")
    await _entry(db_session, status="rejected", rejection_reason="Region not open yet",
                 approved_by_id=admin.id)

    assert (await svc.list_entries(db_session, status="submitted"))["total"] == 1
    assert (await svc.list_entries(db_session, status="approved"))["total"] == 1
    # every rejection reason ever written was invisible in the panel
    rejected = await svc.list_entries(db_session, status="rejected")
    assert rejected["items"][0]["rejection_reason"] == "Region not open yet"
    assert rejected["items"][0]["decided_by_handle"]
    # empty status spans all three
    assert (await svc.list_entries(db_session, status=""))["total"] == 3


async def test_list_rejects_an_unknown_status(db_session):
    with pytest.raises(BadRequest):
        await svc.list_entries(db_session, status="pending")


async def test_list_search_matches_every_identity_the_row_has(db_session):
    await _entry(db_session, x_username="zephyr", telegram_username="zed",
                 telegram_display_name="Zoë", telegram_id=424242)
    await _entry(db_session, x_username="other")

    for needle in ("zephyr", "ZED", "zo", "424242"):
        found = await svc.list_entries(db_session, q=needle)
        assert found["total"] == 1, needle
        assert found["items"][0]["x_username"] == "zephyr"


async def test_list_search_ignores_a_leading_at(db_session):
    await _entry(db_session, x_username="gabriel")
    assert (await svc.list_entries(db_session, q="@gabriel"))["total"] == 1


async def test_list_filters_region_niche_and_has_score(db_session):
    await _entry(db_session, region="europe", niche="trading", score=120.0)
    await _entry(db_session, region="africa", niche="trading")
    await _entry(db_session, region="europe", niche="defi", score=5.0)

    assert (await svc.list_entries(db_session, region="europe"))["total"] == 2
    assert (await svc.list_entries(db_session, niche="trading"))["total"] == 2
    # the 7-of-35 unscored applicants, as a filter
    assert (await svc.list_entries(db_session, has_score=False))["total"] == 1
    assert (await svc.list_entries(db_session, has_score=True))["total"] == 2


async def test_list_sorts_by_score_with_unscored_rows_last(db_session):
    await _entry(db_session, x_username="low", score=10.0)
    await _entry(db_session, x_username="high", score=900.0)
    await _entry(db_session, x_username="none")

    desc = await svc.list_entries(db_session, sort="score", direction="desc")
    assert [r["x_username"] for r in desc["items"]] == ["high", "low", "none"]
    asc = await svc.list_entries(db_session, sort="score", direction="asc")
    # unscored sinks in BOTH directions rather than hijacking the top
    assert [r["x_username"] for r in asc["items"]] == ["low", "high", "none"]


async def test_list_sorts_by_submitted_date(db_session):
    from datetime import timedelta

    old = await _entry(db_session, x_username="old",
                       created_at=utcnow() - timedelta(days=3))
    new = await _entry(db_session, x_username="new", created_at=utcnow())
    asc = await svc.list_entries(db_session, sort="created", direction="asc")
    assert [r["id"] for r in asc["items"]] == [str(old.id), str(new.id)]
    desc = await svc.list_entries(db_session, sort="created", direction="desc")
    assert [r["id"] for r in desc["items"]] == [str(new.id), str(old.id)]


async def test_row_carries_what_the_decision_needs(db_session):
    """Everything here was already on the entry and none of it was on screen."""
    entry = await _entry(
        db_session,
        telegram_username="", telegram_display_name="Dmitri", telegram_id=905004,
        x_username="seed_dmitri", region="africa",
        other_platforms=[{"platform": "youtube", "username": "dmitri15"}],
        total_referrals=3,
        score=330.0, score_updated_at=utcnow(),
        score_data={
            "followers_count": 13491, "friends_count": 7159, "tweets_count": 17656,
            "smart_followers": 60, "register_date": "2012-07-25",
            "description": "builder, shitposter, occasional alpha",
            "name": "Quinn", "verified": False, "category": "builder",
        },
    )
    row = (await svc.list_entries(db_session))["items"][0]

    assert row["id"] == str(entry.id)
    # the fallback identity for the rows that render as a bare "—" today
    assert row["telegram_display_name"] == "Dmitri"
    assert row["telegram_id"] == 905004
    assert row["followers_count"] == 13491
    assert row["register_date"] == "2012-07-25"
    assert row["bio"] == "builder, shitposter, occasional alpha"
    assert row["other_platforms"][0]["platform"] == "youtube"
    assert row["total_referrals"] == 3
    assert row["tier"] is not None


async def test_row_distinguishes_never_fetched_from_no_score(db_session):
    await _entry(db_session, x_username="never")
    await _entry(db_session, x_username="empty", score_updated_at=utcnow())
    rows = {r["x_username"]: r for r in (await svc.list_entries(db_session))["items"]}

    # "pending" — the sign-up fetch hasn't landed
    assert rows["never"]["score"] is None and rows["never"]["score_updated_at"] is None
    # "no score" — the provider answered and had nothing
    assert rows["empty"]["score"] is None and rows["empty"]["score_updated_at"] is not None


async def test_row_resolves_the_referrer_and_counts_the_ring(db_session):
    """Five seeded pending entries share one referral code. That is either a
    good evangelist or a ring, and the reviewer couldn't see it at all."""
    referrer = await _entry(db_session, x_username="promoter", referral_code="RING1234")
    for _ in range(5):
        await _entry(db_session, referral_code_used="RING1234")

    page = await svc.list_entries(db_session, q="RING1234")
    assert page["total"] == 6  # the owner matches on referral_code too
    recruits = [r for r in page["items"] if r["referral_code_used"] == "RING1234"]
    assert len(recruits) == 5
    assert {r["referrer_handle"] for r in recruits} == {"promoter"}
    assert {r["referral_code_uses"] for r in recruits} == {5}
    assert referrer.referral_code == "RING1234"


async def test_row_resolves_a_referrer_who_is_already_a_user(db_session, make_user):
    await make_user(telegram_id=985, referral_code="USERCODE1", x_username="og_user")
    await _entry(db_session, referral_code_used="USERCODE1")
    row = (await svc.list_entries(db_session))["items"][0]
    assert row["referrer_handle"] == "og_user"


async def test_list_caps_the_page_size(db_session):
    page = await svc.list_entries(db_session, limit=10_000)
    assert page["limit"] == svc.MAX_PAGE_SIZE
