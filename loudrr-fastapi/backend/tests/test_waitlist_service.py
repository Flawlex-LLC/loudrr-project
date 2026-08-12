"""Service-level tests for the waitlist use cases (register/status/approve/reject).
These exercise the service + both repositories against the real DB."""
import pytest

from app.core.crypto import sign_x_proof
from app.core.errors import BadRequest, Conflict
from app.core.time_utils import utcnow
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
    return WaitlistRegisterRequest(
        x_proof=_proof(tg_id=tg_id, username=username, x_user_id=x_user_id),
        **kw,
    )


def _tg(id=111, username="alice", first_name="Alice"):
    return {"id": id, "username": username, "first_name": first_name}


async def test_register_creates_entry(db_session):
    result = await svc.register_entry(db_session, tg_user=_tg(), payload=_payload())
    assert result.was_new is True
    assert result.entry.x_username == "alice"
    assert result.entry.status == "submitted"
    assert result.entry.x_verified is True     # OAuth-verified at registration
    assert result.entry.x_user_id == "1"        # from the proof payload
    assert result.entry.referral_code           # a code was generated


async def test_register_idempotent_on_telegram_id(db_session):
    first = await svc.register_entry(db_session, tg_user=_tg(), payload=_payload())
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


async def test_register_rejects_duplicate_x_username(db_session):
    """Two different Telegram accounts trying to claim the same X handle
    must conflict — one X account = one waitlist entry."""
    await svc.register_entry(db_session, tg_user=_tg(id=1), payload=_payload(tg_id=1))
    with pytest.raises(BadRequest):
        await svc.register_entry(
            db_session, tg_user=_tg(id=2, username="bob"),
            # same X handle, different tg id — proof still valid for tg=2
            payload=_payload(tg_id=2, username="alice", x_user_id="1"),
        )


async def test_status_lifecycle(db_session):
    assert (await svc.get_status(db_session, telegram_id=999)).status == "not_registered"
    await svc.register_entry(
        db_session, tg_user=_tg(id=999), payload=_payload(tg_id=999),
    )
    assert (await svc.get_status(db_session, telegram_id=999)).status == "waitlisted"


async def test_approve_creates_user(db_session, make_user):
    admin = await make_user()  # approved_by_id is a real FK -> users.id
    reg = await svc.register_entry(db_session, tg_user=_tg(), payload=_payload())
    user = await svc.approve_entry(
        db_session, entry_id=reg.entry.id, admin_id=admin.id
    )
    assert user.x_username == "alice"
    await db_session.refresh(reg.entry)
    assert reg.entry.status == "approved"
    assert reg.entry.created_user_id == user.id
    # the user now exists -> status flips to approved
    assert (await svc.get_status(db_session, telegram_id=111)).status == "approved"


async def test_approve_twice_conflicts(db_session, make_user):
    admin = await make_user()
    reg = await svc.register_entry(db_session, tg_user=_tg(), payload=_payload())
    await svc.approve_entry(db_session, entry_id=reg.entry.id, admin_id=admin.id)
    with pytest.raises(Conflict):
        await svc.approve_entry(db_session, entry_id=reg.entry.id, admin_id=admin.id)


async def test_reject_sets_status(db_session, make_user):
    admin = await make_user()
    reg = await svc.register_entry(db_session, tg_user=_tg(), payload=_payload())
    entry = await svc.reject_entry(
        db_session, entry_id=reg.entry.id, admin_id=admin.id, reason="spam"
    )
    assert entry.status == "rejected"
    assert entry.rejection_reason == "spam"


# ---- Outbox-parity gap (P1): approve_entry must kick off the TweetScout fetch
# (parity with Django core/admin.py:741) so the new User's tier multiplier is
# populated soon after approval.
async def test_approve_enqueues_tweetscout_fetch(db_session, make_user, monkeypatch):
    captured: list[tuple[str, tuple]] = []

    async def _fake_enqueue(task_name, *args):
        captured.append((task_name, args))
        return True

    monkeypatch.setattr("app.tasks.enqueue.enqueue", _fake_enqueue)

    admin = await make_user()
    reg = await svc.register_entry(db_session, tg_user=_tg(), payload=_payload())
    user = await svc.approve_entry(
        db_session, entry_id=reg.entry.id, admin_id=admin.id
    )
    # exactly one enqueue, for fetch_tweetscout_for_user, with the new user id
    assert captured == [("fetch_tweetscout_for_user", (str(user.id),))]