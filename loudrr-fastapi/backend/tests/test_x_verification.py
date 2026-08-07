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
    assert url.startswith("https://twitter.com/i/oauth2/authorize?")
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


async def test_callback_match_verifies(client, make_user, db_session, monkeypatch):
    user = await make_user(telegram_id=6008, x_username="alice")
    await _seed_state(db_session, user.id, state="st-match")
    _mock_oauth(monkeypatch, me={"id": "111", "username": "alice"})

    r = await client.get("/api/auth/x/callback/", params={"code": "c", "state": "st-match"})
    assert r.status_code == 200
    assert "Connected" in r.text
    assert user.x_verified is True
    assert user.x_verified_at is not None


async def test_callback_mismatch_stores_pending(client, make_user, db_session, monkeypatch):
    user = await make_user(telegram_id=6009, x_username="alice")
    await _seed_state(db_session, user.id, state="st-mismatch")
    _mock_oauth(monkeypatch, me={"id": "222", "username": "bob"})

    r = await client.get("/api/auth/x/callback/", params={"code": "c", "state": "st-mismatch"})
    assert r.status_code == 200
    assert "Different Account" in r.text
    assert user.x_verified is False
    assert user.pending_claimed_x_username == "bob"
    assert user.pending_claimed_x_user_id == "222"


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
