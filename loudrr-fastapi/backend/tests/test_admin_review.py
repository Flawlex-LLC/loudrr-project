"""HTTP contract for the two admin review queues (api/admin_review.py).

The service-level behaviour is covered in test_waitlist_service.py and
test_x_verification.py; what's asserted here is the wire shape the Next.js
panel is built against — the `{items, total, limit, offset}` envelope, the
query parameters, the role gate, and the status codes the UI branches on
(409 on a state-machine refusal, 422 on an over-long public reason).
"""
import uuid

from sqlalchemy import select

from app.models.audit_log import AuditLog
from app.models.outbox_event import OutboxEvent
from app.models.user import User
from app.models.waitlist_entry import WaitlistEntry
from app.models.x_verification_request import XVerificationRequest

WAITLIST = "/api/admin/waitlist/"
XVERIFY = "/api/admin/x-verification/"


async def _entry(db, **kw) -> WaitlistEntry:
    values = dict(
        telegram_id=uuid.uuid4().int % 1_000_000_000,
        x_username=f"x{uuid.uuid4().hex[:8]}",
        referral_code=uuid.uuid4().hex[:10].upper(),
        status="submitted",
    )
    values.update(kw)
    row = WaitlistEntry(**values)
    db.add(row)
    await db.commit()
    return row


async def _xreq(db, user, **kw) -> XVerificationRequest:
    values = dict(
        submitted_x_username="alice", claimed_x_username="alice_real",
        claimed_x_user_id="42", status="PENDING",
    )
    values.update(kw)
    row = XVerificationRequest(user_id=user.id, **values)
    db.add(row)
    await db.commit()
    return row


# ---------------------------------------------------------------------------
# GET /api/admin/waitlist/
# ---------------------------------------------------------------------------
async def test_list_waitlist_returns_the_envelope(client, make_user, db_session):
    admin = await make_user(role="admin")
    for _ in range(4):
        await _entry(db_session)

    r = await client.get(WAITLIST, params={
        "telegram_id": admin.telegram_id, "limit": 2,
    })
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"items", "total", "limit", "offset"}
    assert body["total"] == 4 and len(body["items"]) == 2


async def test_list_waitlist_accepts_every_documented_filter(
    client, make_user, db_session,
):
    admin = await make_user(role="admin")
    await _entry(db_session, x_username="gabriel", region="europe",
                 niche="trading", score=700.0)
    await _entry(db_session, x_username="hiroshi", region="oceania", niche="ai_tech")

    r = await client.get(WAITLIST, params={
        "telegram_id": admin.telegram_id,
        "q": "gab", "status": "submitted", "sort": "score", "dir": "desc",
        "region": "europe", "niche": "trading", "has_score": "true",
        "limit": 10, "offset": 0,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["x_username"] == "gabriel"


async def test_list_waitlist_validates_sort_and_dir(client, make_user):
    admin = await make_user(role="admin")
    for bad in ({"sort": "karma"}, {"dir": "sideways"}):
        r = await client.get(WAITLIST, params={"telegram_id": admin.telegram_id, **bad})
        assert r.status_code == 422


async def test_list_waitlist_rejects_an_unknown_status(client, make_user):
    admin = await make_user(role="admin")
    r = await client.get(WAITLIST, params={
        "telegram_id": admin.telegram_id, "status": "pending",
    })
    assert r.status_code == 400


async def test_list_waitlist_requires_admin(client, make_user):
    user = await make_user(role="")
    r = await client.get(WAITLIST, params={"telegram_id": user.telegram_id})
    assert r.status_code == 403


async def test_waitlist_facets_serve_the_filter_dropdowns(client, make_user):
    admin = await make_user(role="admin")
    r = await client.get("/api/admin/waitlist/facets/", params={
        "telegram_id": admin.telegram_id,
    })
    assert r.status_code == 200
    body = r.json()
    assert "cis_eastern_europe" in body["regions"]
    assert "ai_tech" in body["niches"]
    assert body["statuses"] == ["submitted", "approved", "rejected"]


# ---------------------------------------------------------------------------
# POST /api/admin/waitlist/{id}/reject/ — the public/internal split, over HTTP
# ---------------------------------------------------------------------------
async def test_reject_body_takes_both_fields_and_only_one_is_sent(
    client, make_user, db_session,
):
    admin = await make_user(role="admin")
    entry = await _entry(db_session, x_username="kasper")

    r = await client.post(
        f"/api/admin/waitlist/{entry.id}/reject/",
        params={"telegram_id": admin.telegram_id},
        json={
            "reason": "Not a fit for the beta right now",
            "internal_note": "bot-like: created 4 days ago, 0 original posts",
        },
    )
    assert r.status_code == 200

    ev = (
        await db_session.execute(
            select(OutboxEvent).where(OutboxEvent.event_type == "waitlist_rejected")
        )
    ).scalar_one()
    assert ev.payload["reason"] == "Not a fit for the beta right now"
    assert "bot-like" not in str(ev.payload)

    log = (
        await db_session.execute(
            select(AuditLog).where(AuditLog.action == "reject_waitlist")
        )
    ).scalar_one()
    assert log.detail["internal_note"].startswith("bot-like")


async def test_reject_caps_the_two_fields(client, make_user, db_session):
    """A public reason is a DM (300); an internal note can be evidence (1000)."""
    admin = await make_user(role="admin")
    entry = await _entry(db_session)

    too_long_public = await client.post(
        f"/api/admin/waitlist/{entry.id}/reject/",
        params={"telegram_id": admin.telegram_id},
        json={"reason": "x" * 301},
    )
    assert too_long_public.status_code == 422

    too_long_internal = await client.post(
        f"/api/admin/waitlist/{entry.id}/reject/",
        params={"telegram_id": admin.telegram_id},
        json={"internal_note": "x" * 1001},
    )
    assert too_long_internal.status_code == 422

    ok = await client.post(
        f"/api/admin/waitlist/{entry.id}/reject/",
        params={"telegram_id": admin.telegram_id},
        json={"reason": "x" * 300, "internal_note": "y" * 1000},
    )
    assert ok.status_code == 200


# ---------------------------------------------------------------------------
# POST /api/admin/waitlist/{id}/reopen/
# ---------------------------------------------------------------------------
async def test_reopen_puts_a_rejection_back_in_the_queue(client, make_user, db_session):
    admin = await make_user(role="admin")
    entry = await _entry(db_session, status="rejected", rejection_reason="mis-clicked")

    r = await client.post(
        f"/api/admin/waitlist/{entry.id}/reopen/",
        params={"telegram_id": admin.telegram_id},
        json={"internal_note": "wrong row"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "submitted"

    await db_session.refresh(entry)
    assert entry.rejection_reason == ""
    log = (
        await db_session.execute(
            select(AuditLog).where(AuditLog.action == "reopen_waitlist")
        )
    ).scalar_one()
    assert log.actor_id == admin.id


async def test_reopen_409s_on_a_submitted_entry(client, make_user, db_session):
    admin = await make_user(role="admin")
    entry = await _entry(db_session)
    r = await client.post(
        f"/api/admin/waitlist/{entry.id}/reopen/",
        params={"telegram_id": admin.telegram_id}, json={},
    )
    assert r.status_code == 409


async def test_reopen_409s_once_a_user_exists(client, make_user, db_session):
    admin = await make_user(role="admin")
    ghost = await make_user()
    entry = await _entry(db_session, status="rejected", created_user_id=ghost.id)
    r = await client.post(
        f"/api/admin/waitlist/{entry.id}/reopen/",
        params={"telegram_id": admin.telegram_id}, json={},
    )
    assert r.status_code == 409


async def test_reopen_404s_on_an_unknown_id(client, make_user):
    admin = await make_user(role="admin")
    r = await client.post(
        f"/api/admin/waitlist/{uuid.uuid4()}/reopen/",
        params={"telegram_id": admin.telegram_id}, json={},
    )
    assert r.status_code == 404


async def test_reopen_requires_admin(client, make_user, db_session):
    user = await make_user(role="")
    entry = await _entry(db_session, status="rejected")
    r = await client.post(
        f"/api/admin/waitlist/{entry.id}/reopen/",
        params={"telegram_id": user.telegram_id}, json={},
    )
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# POST /api/admin/waitlist/{id}/refresh-score/
# ---------------------------------------------------------------------------
async def test_refresh_score_runs_inline_and_returns_the_row(
    client, make_user, db_session, monkeypatch,
):
    from app.services import scores as scores_svc

    admin = await make_user(role="admin")
    entry = await _entry(db_session, x_username="esme")

    async def _fake_fetch(db, entry_id):
        row = await db.get(WaitlistEntry, uuid.UUID(str(entry_id)))
        row.score = 250.0
        await db.commit()
        return "found"

    monkeypatch.setattr(scores_svc, "fetch_waitlist_score", _fake_fetch)

    r = await client.post(
        f"/api/admin/waitlist/{entry.id}/refresh-score/",
        params={"telegram_id": admin.telegram_id},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["queued"] is False and body["result"] == "found"
    assert body["entry"]["score"] == 250.0
    assert body["entry"]["tier"]


async def test_refresh_score_requires_admin(client, make_user, db_session):
    user = await make_user(role="")
    entry = await _entry(db_session)
    r = await client.post(
        f"/api/admin/waitlist/{entry.id}/refresh-score/",
        params={"telegram_id": user.telegram_id},
    )
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# GET /api/admin/x-verification/
# ---------------------------------------------------------------------------
async def test_list_x_verifications_returns_the_enriched_envelope(
    client, make_user, db_session,
):
    admin = await make_user(role="admin")
    holder = await make_user(telegram_username="elena", x_username="seed_elena")
    chen = await make_user(telegram_username="chen", x_username="seed_chen",
                           tweetscout_score=90.0)
    await _xreq(db_session, chen, submitted_x_username="seed_chen",
                claimed_x_username="seed_elena", claimed_x_user_id="99")

    r = await client.get(XVERIFY, params={"telegram_id": admin.telegram_id})
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"items", "total", "limit", "offset"}
    row = body["items"][0]
    assert row["claimed_x_user_id"] == "99"
    assert row["user_tier"] and row["user_score"] == 90.0
    assert row["user_is_banned"] is False
    assert row["prior_requests"] == []
    # surfaced in the LIST so Approve can be disabled before it 409s
    assert row["claimed_handle_taken_by"]["user_id"] == str(holder.id)


async def test_list_x_verifications_filters_and_pages(client, make_user, db_session):
    admin = await make_user(role="admin")
    for i in range(3):
        await _xreq(db_session, await make_user(telegram_id=8100 + i))
    await _xreq(db_session, await make_user(telegram_id=8200), status="REJECTED",
                admin_notes="impersonation")

    pending = await client.get(XVERIFY, params={
        "telegram_id": admin.telegram_id, "status": "PENDING", "limit": 2,
    })
    assert pending.json()["total"] == 3 and len(pending.json()["items"]) == 2

    history = await client.get(XVERIFY, params={
        "telegram_id": admin.telegram_id, "status": "REJECTED",
    })
    assert history.json()["items"][0]["admin_notes"] == "impersonation"


async def test_list_x_verifications_requires_admin(client, make_user):
    user = await make_user(role="")
    r = await client.get(XVERIFY, params={"telegram_id": user.telegram_id})
    assert r.status_code == 403


async def test_x_verification_reject_body_splits_public_and_internal(
    client, make_user, db_session,
):
    admin = await make_user(role="admin")
    user = await make_user(x_verified=True)
    req = await _xreq(db_session, user)

    r = await client.post(
        f"/api/admin/x-verification/{req.id}/reject/",
        params={"telegram_id": admin.telegram_id},
        json={
            "reason": "We couldn't verify your X account",
            "internal_note": "AUDIT TEST: cannot confirm ownership",
        },
    )
    assert r.status_code == 200

    await db_session.refresh(req)
    assert req.admin_notes == "AUDIT TEST: cannot confirm ownership"

    ev = (
        await db_session.execute(
            select(OutboxEvent).where(OutboxEvent.event_type == "x_verification_rejected")
        )
    ).scalar_one()
    assert ev.payload["reason"] == "We couldn't verify your X account"
    assert "AUDIT TEST" not in str(ev.payload)

    refreshed = (
        await db_session.execute(select(User).where(User.id == user.id))
    ).scalar_one()
    assert refreshed.x_verified is False
