"""Tests for Ch11 — X verification (OAuth start, callback, mismatch, approve).

The X OAuth network calls (token exchange, /users/me) are mocked. The
authorize URL and PKCE state are real (no network).
"""
import uuid
from datetime import timedelta

import pytest

from app.core.time_utils import utcnow
from app.integrations import x_oauth
from app.models.x_oauth_state import XOAuthState
from app.models.x_verification_request import (
    XVerificationStatus,
)
from app.repositories.x_verification_request import XVerificationRequestRepository
from app.services import x_verification as svc


# ---- POST /x-oauth/start/ ----
async def test_start_returns_authorize_url(client, make_user, db_session):
    user = await make_user(telegram_id=6001, x_username="alice")
    r = await client.post("/x-oauth/start/", params={"telegram_id": 6001})
    assert r.status_code == 200
    url = r.json()["authorize_url"]
    assert url.startswith("https://x.com/i/oauth2/authorize?")
    assert "code_challenge_method=S256" in url
    assert "state=" in url
    # a PKCE state row was persisted for the callback to consume
    state_row = await db_session.get(
        XOAuthState, url.split("state=")[1].split("&")[0]
    )
    assert state_row is not None
    assert state_row.user_id == user.id


async def test_start_banned_403(client, make_user):
    await make_user(telegram_id=6002, is_banned=True)
    r = await client.post("/x-oauth/start/", params={"telegram_id": 6002})
    assert r.status_code == 403


async def test_start_already_verified_400(client, make_user):
    await make_user(telegram_id=6003, x_verified=True)
    r = await client.post("/x-oauth/start/", params={"telegram_id": 6003})
    assert r.status_code == 400


async def test_start_not_configured_503(client, make_user, monkeypatch):
    await make_user(telegram_id=6004)
    monkeypatch.setattr(x_oauth, "is_configured", lambda: False)
    r = await client.post("/x-oauth/start/", params={"telegram_id": 6004})
    assert r.status_code == 503


# ---- POST /x-verification/confirm-mismatch/ ----
async def test_confirm_mismatch_creates_request(client, make_user, db_session):
    user = await make_user(
        telegram_id=6005, x_username="alice",
        pending_claimed_x_username="bob", pending_claimed_x_user_id="999",
    )
    r = await client.post(
        "/x-verification/confirm-mismatch/", params={"telegram_id": 6005}
    )
    assert r.status_code == 200
    assert r.json() == {"status": "pending_review"}

    req = await XVerificationRequestRepository(db_session).get(user_id=user.id)
    assert req is not None
    assert req.status == XVerificationStatus.PENDING.value
    assert req.claimed_x_username == "bob"
    assert req.claimed_x_user_id == "999"
    # pending prompt cleared
    assert user.pending_claimed_x_username == ""

    # and /user/ now reports a pending review
    r2 = await client.get("/user/", params={"telegram_id": 6005})
    assert r2.json()["x_verification_pending_review"] is True


async def test_confirm_mismatch_without_pending_400(client, make_user):
    await make_user(telegram_id=6006)
    r = await client.post(
        "/x-verification/confirm-mismatch/", params={"telegram_id": 6006}
    )
    assert r.status_code == 400


# ---- POST /x-verification/cancel-mismatch/ ----
async def test_cancel_mismatch_clears(client, make_user, db_session):
    user = await make_user(
        telegram_id=6007, pending_claimed_x_username="bob", pending_claimed_x_user_id="9"
    )
    r = await client.post(
        "/x-verification/cancel-mismatch/", params={"telegram_id": 6007}
    )
    assert r.status_code == 200
    assert r.json() == {"status": "cleared"}
    assert user.pending_claimed_x_username == ""
    assert user.pending_claimed_x_user_id == ""


# ---- GET /api/auth/x/callback/ ----
def _mock_oauth(monkeypatch, *, token="tok", me=None):
    async def _exchange(code, verifier):
        return token

    async def _me(access_token):
        return me

    monkeypatch.setattr(x_oauth, "exchange_code_for_token", _exchange)
    monkeypatch.setattr(x_oauth, "fetch_me", _me)


async def _seed_state(db, user_id, state="teststate", ttl=600):
    db.add(
        XOAuthState(
            state=state, user_id=user_id, code_verifier="verifier",
            expires_at=utcnow() + timedelta(seconds=ttl),
        )
    )
    await db.commit()


def _confirm_token(html_text: str) -> str:
    import re

    m = re.search(r'name="token" value="([^"]+)"', html_text)
    assert m, html_text[:400]
    return m.group(1)


async def _decide(client, token, decision):
    return await client.post(
        "/api/auth/x/confirm/",
        content=f"token={token}&decision={decision}",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )


async def test_callback_match_verifies_after_confirm(client, make_user, db_session, monkeypatch):
    user = await make_user(telegram_id=6008, x_username="alice", telegram_username="alice_tg")
    await _seed_state(db_session, user.id, state="st-match")
    _mock_oauth(monkeypatch, me={"id": "111", "username": "alice"})

    r = await client.get("/api/auth/x/callback/", params={"code": "c", "state": "st-match"})
    assert r.status_code == 200
    assert "Confirm this connection" in r.text
    assert "@alice" in r.text and "@alice_tg" in r.text
    await db_session.refresh(user)
    assert user.x_verified is False          # nothing applied before the confirm

    r = await _decide(client, _confirm_token(r.text), "confirm")
    assert r.status_code == 200
    assert "Connected" in r.text
    await db_session.refresh(user)
    assert user.x_verified is True
    assert user.x_verified_at is not None


async def test_callback_mismatch_stores_pending_after_confirm(client, make_user, db_session, monkeypatch):
    user = await make_user(telegram_id=6009, x_username="alice")
    await _seed_state(db_session, user.id, state="st-mismatch")
    _mock_oauth(monkeypatch, me={"id": "222", "username": "bob"})

    r = await client.get("/api/auth/x/callback/", params={"code": "c", "state": "st-mismatch"})
    r = await _decide(client, _confirm_token(r.text), "confirm")
    assert r.status_code == 200
    assert "Different Account" in r.text
    await db_session.refresh(user)
    assert user.x_verified is False
    assert user.pending_claimed_x_username == "bob"
    assert user.pending_claimed_x_user_id == "222"


async def test_forwarded_connect_link_gives_the_attacker_nothing_without_a_confirm(
    client, make_user, db_session, monkeypatch,
):
    """Mallory typed the victim's handle, started Connect X and forwarded the
    authorize link. The victim authorizing on X must NOT verify Mallory: the
    page names Mallory's Telegram account, and only a confirm applies it."""
    mallory = await make_user(telegram_id=6020, x_username="victim_whale", telegram_username="mallory")
    await _seed_state(db_session, mallory.id, state="st-phish")
    _mock_oauth(monkeypatch, me={"id": "999", "username": "victim_whale"})

    page = await client.get("/api/auth/x/callback/", params={"code": "c", "state": "st-phish"})
    assert "@mallory" in page.text             # the victim sees WHO gets it
    await db_session.refresh(mallory)
    assert mallory.x_verified is False
    assert mallory.pending_claimed_x_username == ""

    cancelled = await _decide(client, _confirm_token(page.text), "cancel")
    assert "Nothing Was Connected" in cancelled.text
    await db_session.refresh(mallory)
    assert mallory.x_verified is False
    # and the token is spent
    again = await _decide(client, _confirm_token(page.text), "confirm")
    assert again.status_code == 404
    await db_session.refresh(mallory)
    assert mallory.x_verified is False


async def test_confirm_token_is_single_use_and_expires(client, make_user, db_session, monkeypatch):
    from app.models.x_oauth_confirmation import XOAuthConfirmation

    user = await make_user(telegram_id=6021, x_username="carol")
    await _seed_state(db_session, user.id, state="st-once")
    _mock_oauth(monkeypatch, me={"id": "333", "username": "carol"})
    token = _confirm_token((await client.get("/api/auth/x/callback/", params={"code": "c", "state": "st-once"})).text)

    assert (await _decide(client, token, "confirm")).status_code == 200
    assert (await _decide(client, token, "confirm")).status_code == 404
    assert (await _decide(client, "not-a-token", "confirm")).status_code == 404

    # an expired confirmation is refused and changes nothing
    await _seed_state(db_session, user.id, state="st-old")
    user.x_verified = False
    await db_session.commit()
    token = _confirm_token((await client.get("/api/auth/x/callback/", params={"code": "c", "state": "st-old"})).text)
    from sqlalchemy import update
    await db_session.execute(
        update(XOAuthConfirmation).values(expires_at=utcnow() - timedelta(seconds=1))
    )
    await db_session.commit()
    assert (await _decide(client, token, "confirm")).status_code == 404
    await db_session.refresh(user)
    assert user.x_verified is False


async def test_confirm_page_escapes_the_telegram_label_and_refuses_framing(
    client, make_user, db_session, monkeypatch,
):
    user = await make_user(telegram_id=6022, x_username="dave", display_name="<script>x</script>")
    await _seed_state(db_session, user.id, state="st-xss")
    _mock_oauth(monkeypatch, me={"id": "444", "username": "dave"})

    r = await client.get("/api/auth/x/callback/", params={"code": "c", "state": "st-xss"})
    assert "<script>x</script>" not in r.text
    assert "&lt;script&gt;" in r.text
    assert r.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert "token=" not in (r.headers.get("location") or "")


async def test_callback_error_param_400(client):
    r = await client.get("/api/auth/x/callback/", params={"error": "access_denied"})
    assert r.status_code == 400


async def test_callback_unknown_state_400(client, monkeypatch):
    _mock_oauth(monkeypatch, me={"id": "1", "username": "x"})
    r = await client.get("/api/auth/x/callback/", params={"code": "c", "state": "nope"})
    assert r.status_code == 400
    assert "Session Expired" in r.text


async def test_callback_token_failure_502(client, make_user, db_session, monkeypatch):
    user = await make_user(telegram_id=6010, x_username="alice")
    await _seed_state(db_session, user.id, state="st-tokfail")
    _mock_oauth(monkeypatch, token=None)  # token exchange fails
    r = await client.get("/api/auth/x/callback/", params={"code": "c", "state": "st-tokfail"})
    assert r.status_code == 502


async def test_callback_expired_state_400(client, make_user, db_session, monkeypatch):
    user = await make_user(telegram_id=6011, x_username="alice")
    await _seed_state(db_session, user.id, state="st-exp", ttl=-10)  # already expired
    _mock_oauth(monkeypatch, me={"id": "1", "username": "alice"})
    r = await client.get("/api/auth/x/callback/", params={"code": "c", "state": "st-exp"})
    assert r.status_code == 400


# ---- admin approve (service-level; UI wired in Ch17) ----
async def test_approve_x_verification(db_session, make_user):
    admin = await make_user(telegram_id=6099)  # reviewed_by_id FK → a real user
    user = await make_user(telegram_id=6012, x_username="alice")
    repo = XVerificationRequestRepository(db_session)
    req = await repo.create(
        user_id=user.id, submitted_x_username="alice",
        claimed_x_username="bob", claimed_x_user_id="999",
    )
    await db_session.commit()

    result = await svc.approve_x_verification(
        db_session, request_id=req.id, admin_id=admin.id
    )
    assert result.status == XVerificationStatus.APPROVED.value
    assert user.x_username == "bob"
    assert user.x_verified is True
    assert user.x_verified_at is not None


async def test_approve_conflict_when_handle_taken(db_session, make_user):
    from app.core.errors import Conflict

    _taken = await make_user(telegram_id=6013, x_username="bob")
    user = await make_user(telegram_id=6014, x_username="alice")
    repo = XVerificationRequestRepository(db_session)
    req = await repo.create(
        user_id=user.id, submitted_x_username="alice",
        claimed_x_username="bob", claimed_x_user_id="999",
    )
    await db_session.commit()

    with pytest.raises(Conflict):
        await svc.approve_x_verification(
            db_session, request_id=req.id, admin_id=uuid.uuid4()
        )


async def test_approve_x_verification_writes_an_audit_row(db_session, make_user):
    """Rejections were audited and approvals weren't — an approval hands a user
    a verified handle, so it has to be in the trail too."""
    from sqlalchemy import select

    from app.models.audit_log import AuditLog
    from app.services import x_verification as xverify

    admin = await make_user(telegram_id=6098, role="admin")
    user = await make_user(telegram_id=6097, x_username="old_handle")
    repo = XVerificationRequestRepository(db_session)
    req = await repo.create(
        user_id=user.id, submitted_x_username="old_handle",
        claimed_x_username="new_handle", claimed_x_user_id="4242",
    )
    await db_session.commit()

    await xverify.approve_x_verification(db_session, request_id=req.id, admin_id=admin.id)

    log = (
        await db_session.execute(select(AuditLog).where(AuditLog.action == "approve_x_verification"))
    ).scalar_one()
    assert log.actor_id == admin.id
    assert log.target_id == req.id
    assert log.detail["x_username"] == "new_handle"


# ============================================================================
# THE REVIEW QUEUE — the identity call.
#
# The list gave a reviewer two handles and a date. Everything asserted below
# was already in the database and simply wasn't being read: who the requester
# is, what they asked for before and were told, and whether the handle they
# want already belongs to somebody else (the case approve answers with a 409,
# discoverable only by firing the irreversible action).
# ============================================================================


async def _request(db, user, **kw):
    repo = XVerificationRequestRepository(db)
    values = dict(
        submitted_x_username="alice", claimed_x_username="alice_real",
        claimed_x_user_id="1234567890",
    )
    values.update(kw)
    req = await repo.create(user_id=user.id, **values)
    await db.commit()
    return req


async def test_list_returns_an_envelope_with_a_total(db_session, make_user):
    for i in range(5):
        await _request(db_session, await make_user(telegram_id=7100 + i))

    page = await svc.list_requests(db_session, limit=2)
    assert len(page["items"]) == 2
    assert page["total"] == 5
    assert page["limit"] == 2 and page["offset"] == 0
    second = await svc.list_requests(db_session, limit=2, offset=2)
    assert {r["id"] for r in second["items"]} & {r["id"] for r in page["items"]} == set()


async def test_list_filters_by_status_and_spans_all_with_an_empty_one(
    db_session, make_user,
):
    await _request(db_session, await make_user(telegram_id=7201))
    await _request(db_session, await make_user(telegram_id=7202), status="APPROVED")
    await _request(db_session, await make_user(telegram_id=7203), status="REJECTED",
                   admin_notes="impersonation attempt")

    assert (await svc.list_requests(db_session, status="PENDING"))["total"] == 1
    assert (await svc.list_requests(db_session, status="APPROVED"))["total"] == 1
    assert (await svc.list_requests(db_session, status=""))["total"] == 3


async def test_list_rejects_an_unknown_status(db_session):
    from app.core.errors import BadRequest

    with pytest.raises(BadRequest):
        await svc.list_requests(db_session, status="pending")


async def test_list_search_covers_both_handles_and_the_user(db_session, make_user):
    user = await make_user(telegram_id=7300, telegram_username="zara", x_username="seed_zara")
    await _request(db_session, user, submitted_x_username="seed_zara",
                   claimed_x_username="seed_zaraxbt")
    await _request(db_session, await make_user(telegram_id=7301),
                   submitted_x_username="nobody", claimed_x_username="nobody_else")

    for needle in ("zaraxbt", "seed_zara", "zara"):
        assert (await svc.list_requests(db_session, q=needle))["total"] == 1, needle


async def test_row_carries_the_users_standing(db_session, make_user):
    """score/tier/credits/banned/verified/joined — the difference between a
    real user with a rename and a sock puppet."""
    from decimal import Decimal

    user = await make_user(
        telegram_id=7400, telegram_username="sybil", x_username="seed_sybil",
        credits=Decimal("42"), is_banned=True, x_verified=False,
        tweetscout_score=311.0, display_name="Sybil",
    )
    await _request(db_session, user, submitted_x_username="seed_sybil",
                   claimed_x_username="seed_real_influencer")

    row = (await svc.list_requests(db_session))["items"][0]
    assert row["user_telegram_username"] == "sybil"
    assert row["user_display_name"] == "Sybil"
    assert row["user_score"] == 311.0
    assert row["user_tier"]
    assert row["user_credits"] == 42.0
    # a banned impersonator asking to adopt a handle is exactly what this
    # queue exists to catch, and it wasn't on screen
    assert row["user_is_banned"] is True
    assert row["user_x_verified"] is False
    assert row["user_created_at"]
    # the immutable numeric id, so a rename still resolves to the account
    assert row["claimed_x_user_id"] == "1234567890"


async def test_row_lists_the_users_prior_requests_with_their_notes(db_session, make_user):
    """A seeded user is on his third handle after a rejection; the note the
    last reviewer left is the whole reason to show history."""
    user = await make_user(telegram_id=7500, telegram_username="yusuf")
    await _request(
        db_session, user, submitted_x_username="seed_yusuf",
        claimed_x_username="seed_yusuf_old", status="REJECTED",
        admin_notes="OAuth'd an old account; asked user to retry with @seed_yusuf",
    )
    current = await _request(
        db_session, user, submitted_x_username="seed_yusuf",
        claimed_x_username="seed_yusuf_",
    )
    # another user's history must not bleed in
    await _request(db_session, await make_user(telegram_id=7501))

    row = next(
        r for r in (await svc.list_requests(db_session))["items"]
        if r["id"] == str(current.id)
    )
    assert len(row["prior_requests"]) == 1
    prior = row["prior_requests"][0]
    assert prior["status"] == "REJECTED"
    assert prior["claimed_x_username"] == "seed_yusuf_old"
    assert "asked user to retry" in prior["admin_notes"]
    # the request never lists itself
    assert current.id not in {p["id"] for p in row["prior_requests"]}


async def test_row_flags_a_claimed_handle_another_user_already_holds(
    db_session, make_user,
):
    """approve_x_verification 409s on exactly this. Before, the only way to
    find out was to fire the irreversible action and read the toast."""
    holder = await make_user(telegram_id=7600, telegram_username="elena",
                             x_username="seed_elena")
    chen = await make_user(telegram_id=7601, telegram_username="chen",
                           x_username="seed_chen")
    await _request(db_session, chen, submitted_x_username="seed_chen",
                   claimed_x_username="seed_elena")

    row = (await svc.list_requests(db_session))["items"][0]
    taken = row["claimed_handle_taken_by"]
    assert taken is not None
    assert taken["user_id"] == str(holder.id)
    assert taken["telegram_username"] == "elena"

    # and the flag agrees with what approve actually does
    from app.core.errors import Conflict

    req_id = row["id"]
    with pytest.raises(Conflict):
        await svc.approve_x_verification(
            db_session, request_id=uuid.UUID(req_id), admin_id=uuid.uuid4()
        )


async def test_claimed_handle_taken_by_ignores_the_requester_themselves(
    db_session, make_user,
):
    """A user re-requesting the handle they already hold isn't a clash."""
    user = await make_user(telegram_id=7700, x_username="seed_diego_eth")
    await _request(db_session, user, submitted_x_username="seed_diego",
                   claimed_x_username="seed_diego_eth")
    row = (await svc.list_requests(db_session))["items"][0]
    assert row["claimed_handle_taken_by"] is None


async def test_clash_detection_is_case_insensitive(db_session, make_user):
    await make_user(telegram_id=7800, x_username="SeedElena")
    await _request(db_session, await make_user(telegram_id=7801),
                   claimed_x_username="seedelena")
    assert (await svc.list_requests(db_session))["items"][0]["claimed_handle_taken_by"]


async def test_row_survives_an_empty_submitted_handle(db_session, make_user):
    """One seeded request has submitted_x_username="" (the user never had a
    handle). The table rendered a bare "@" for it."""
    user = await make_user(telegram_id=7900, telegram_username="amir", x_username=None)
    await _request(db_session, user, submitted_x_username="",
                   claimed_x_username="seed_amir_builds")

    row = (await svc.list_requests(db_session))["items"][0]
    assert row["submitted_x_username"] == ""
    assert row["claimed_x_username"] == "seed_amir_builds"
    assert row["user_x_username"] == ""


# ---- the public reason / internal note split ----
async def test_reject_dms_the_public_reason_and_keeps_the_note_internal(
    db_session, make_user,
):
    from sqlalchemy import select

    from app.models.audit_log import AuditLog
    from app.models.outbox_event import OutboxEvent
    from app.services import admin as admin_svc

    admin_user = await make_user(telegram_id=7950, role="admin")
    user = await make_user(telegram_id=7951, x_verified=True,
                           pending_claimed_x_username="other")
    req = await _request(db_session, user)

    await admin_svc.reject_x_verification(
        db_session, admin_id=admin_user.id, request_id=req.id,
        reason="We couldn't verify your X account",
        internal_note="Impersonation attempt: claimed handle belongs to a known influencer",
    )
    await db_session.refresh(req)

    assert req.status == "REJECTED"
    # the internal note lands on the admin-only column the next reviewer reads
    assert req.admin_notes.startswith("Impersonation attempt")

    ev = (
        await db_session.execute(
            select(OutboxEvent).where(OutboxEvent.event_type == "x_verification_rejected")
        )
    ).scalar_one()
    assert ev.payload["reason"] == "We couldn't verify your X account"
    assert "Impersonation" not in str(ev.payload)

    log = (
        await db_session.execute(
            select(AuditLog).where(AuditLog.action == "reject_x_verification")
        )
    ).scalar_one()
    assert log.detail["reason"] == "We couldn't verify your X account"
    assert log.detail["internal_note"].startswith("Impersonation attempt")


async def test_legacy_notes_kwarg_is_treated_as_internal(db_session, make_user):
    """The SQLAdmin panel and older callers still pass `notes=`. Everyone who
    ever wrote one believed it was internal — honour that reading."""
    from sqlalchemy import select

    from app.models.outbox_event import OutboxEvent
    from app.services import admin as admin_svc

    admin_user = await make_user(telegram_id=7960, role="admin")
    user = await make_user(telegram_id=7961)
    req = await _request(db_session, user)

    await admin_svc.reject_x_verification(
        db_session, admin_id=admin_user.id, request_id=req.id, notes="known scammer",
    )
    await db_session.refresh(req)
    assert req.admin_notes == "known scammer"

    ev = (
        await db_session.execute(
            select(OutboxEvent).where(OutboxEvent.event_type == "x_verification_rejected")
        )
    ).scalar_one()
    assert ev.payload["reason"] == ""
    assert "scammer" not in str(ev.payload)
