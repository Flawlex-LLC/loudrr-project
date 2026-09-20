"""Launch-week waitlist audit (2026-09-17) — each test pins a bug found in the
live end-to-end run or the code review, so it can't come back:

  * approving a referred applicant crashed (User has no total_referrals), and
    referrals made while the referrer was still waitlisted were never counted
  * rejected applicants were told they're "waitlisted" forever
  * an account that already has access could create an un-approvable entry
  * the OAuth proof row outlived registration
  * the public share page had no stored-score source and no membership check
  * the sign-up funnel was rate-limited per IP (carrier NAT locks users out)
  * tier retunes never reached the worker; /settings/ hid the multipliers
  * the provider's answer for ANOTHER X account could be credited
  * forged proofs/initData with the dev key or an empty bot token
"""
import json
from datetime import timedelta
from urllib.parse import urlencode

import pytest
from sqlalchemy import select
from starlette.requests import Request

from app.core.config import settings
from app.core.crypto import sign_x_proof, verify_x_proof
from app.core.limiter import telegram_user_key
from app.core.telegram_auth import verify_init_data
from app.core.time_utils import utcnow
from app.models.audit_log import AuditLog
from app.models.site_setting import SiteSetting
from app.models.waitlist_entry import WaitlistEntry
from app.models.waitlist_oauth_proof import WaitlistOAuthProof
from app.repositories.x_profile import XProfileRepository
from app.schemas.waitlist import WaitlistRegisterRequest
from app.services import scores as scores_svc
from app.services import site_settings
from app.services import tier
from app.services import waitlist as svc


def _tg(tg_id, username="u"):
    return {"id": tg_id, "username": username, "first_name": username}


def _payload(tg_id, handle, x_id, **kw):
    proof = sign_x_proof({
        "tg_id": tg_id, "x_username": handle, "x_user_id": x_id,
        "iat": int(utcnow().timestamp()),
    })
    return WaitlistRegisterRequest(x_proof=proof, **kw)


async def _register(db, tg_id, handle, x_id, **kw):
    payload = _payload(tg_id, handle, x_id, **kw)
    # the X account owner confirmed the link in the browser (register requires it)
    db.add(WaitlistOAuthProof(telegram_id=tg_id, proof=payload.x_proof, confirmed_at=utcnow()))
    await db.commit()
    return (await svc.register_entry(db, tg_user=_tg(tg_id, handle), payload=payload)).entry


# ---------------------------------------------------------------------------
# referrals
# ---------------------------------------------------------------------------
async def test_referral_made_while_referrer_waitlisted_is_credited_at_approval(db_session, make_user):
    admin = await make_user()
    referrer = await _register(db_session, 101, "referrer", "1001")
    referee = await _register(db_session, 102, "referee", "1002", referral_code=referrer.referral_code.lower())
    assert referee.referrer_id is None                      # no User existed yet
    assert referee.referral_code_used == referrer.referral_code  # normalized

    await svc.approve_entry(db_session, entry_id=referrer.id, admin_id=admin.id)
    await svc.approve_entry(db_session, entry_id=referee.id, admin_id=admin.id)

    await db_session.refresh(referrer)
    assert referrer.total_referrals == 1


async def test_approving_applicant_referred_by_an_approved_user_does_not_crash(db_session, make_user):
    """Was a 500 (AttributeError: User has no total_referrals) that rolled the
    approval back — the applicant could never be approved."""
    admin = await make_user()
    referrer = await _register(db_session, 111, "whale", "1111")
    await svc.approve_entry(db_session, entry_id=referrer.id, admin_id=admin.id)
    referee = await _register(db_session, 112, "fan", "1112", referral_code=referrer.referral_code)
    assert referee.referrer_id is not None                  # referrer is a User now

    user = await svc.approve_entry(db_session, entry_id=referee.id, admin_id=admin.id)

    assert user.telegram_id == 112
    await db_session.refresh(referrer)
    assert referrer.total_referrals == 1


async def test_unknown_or_garbage_referral_codes_are_ignored(db_session):
    entry = await _register(db_session, 121, "nobody", "1121", referral_code="not-a-code-at-all-really")
    assert entry.referral_code_used == ""


async def test_approve_and_reject_write_audit_logs(db_session, make_user):
    admin = await make_user()
    a = await _register(db_session, 131, "approve_me", "1131")
    b = await _register(db_session, 132, "reject_me", "1132")
    await svc.approve_entry(db_session, entry_id=a.id, admin_id=admin.id)
    await svc.reject_entry(db_session, entry_id=b.id, admin_id=admin.id, reason="spam")
    actions = {
        (row.action, row.target_id)
        for row in (await db_session.execute(select(AuditLog))).scalars()
    }
    assert ("approve_waitlist", a.id) in actions
    assert ("reject_waitlist", b.id) in actions


# ---------------------------------------------------------------------------
# status / registration edges
# ---------------------------------------------------------------------------
async def test_rejected_applicant_sees_rejected_with_reason(client, db_session, make_user):
    admin = await make_user()
    entry = await _register(db_session, 141, "rejected_one", "1141")
    await svc.reject_entry(db_session, entry_id=entry.id, admin_id=admin.id, reason="bots")
    body = (await client.get("/waitlist/status/", params={"telegram_id": 141})).json()
    assert body["status"] == "rejected"
    assert body["reason"] == "bots"
    assert body["x_username"] == "rejected_one"


async def test_account_with_access_but_no_entry_cannot_register(db_session, make_user):
    await make_user(telegram_id=151)  # e.g. a seeded admin
    from app.core.errors import BadRequest
    with pytest.raises(BadRequest):
        await _register(db_session, 151, "admin_handle", "1151")
    assert (await db_session.execute(select(WaitlistEntry))).scalars().first() is None


async def test_registering_discards_the_oauth_proof_row(db_session):
    await _register(db_session, 161, "fresh", "1161")   # stores the confirmed row first
    db_session.expire_all()
    assert await db_session.get(WaitlistOAuthProof, 161) is None


# ---------------------------------------------------------------------------
# public share card
# ---------------------------------------------------------------------------
async def test_public_card_serves_stored_score_for_waitlisted_handles_only(client, db_session, make_user):
    entry = await _register(db_session, 171, "sharer", "1171")
    entry.score = 587.2
    entry.score_updated_at = utcnow()
    entry.score_data = {"smart_followers": 618, "top_followers": [{"username": "notthreadguy"}]}
    await db_session.commit()

    r = await client.get("/waitlist/card/SHARER/")  # case-insensitive, no auth
    assert r.status_code == 200
    assert r.json() == {
        "x_username": "sharer", "score": 587.2, "tier": "Based",
        "followers": ["notthreadguy"], "followers_count": 618,
        "referral_code": entry.referral_code,
    }
    assert (await client.get("/waitlist/card/elonmusk/")).status_code == 404
    assert (await client.get("/waitlist/card/bad%20handle!/")).status_code == 404

    admin = await make_user()
    rejected = await _register(db_session, 172, "rejected_sharer", "1172")
    await svc.reject_entry(db_session, entry_id=rejected.id, admin_id=admin.id)
    assert (await client.get("/waitlist/card/rejected_sharer/")).status_code == 404


async def test_public_card_for_an_approved_user_reads_the_user_score(client, db_session, make_user):
    admin = await make_user()
    entry = await _register(db_session, 181, "approved_sharer", "1181")
    user = await svc.approve_entry(db_session, entry_id=entry.id, admin_id=admin.id)
    user.tweetscout_score = 900
    user.tweetscout_last_updated = utcnow()
    await db_session.commit()
    body = (await client.get("/waitlist/card/approved_sharer/")).json()
    assert body["score"] == 900 and body["tier"] == "OG"


# ---------------------------------------------------------------------------
# rate limiting is per Telegram user
# ---------------------------------------------------------------------------
def _request(headers=None, query="", client_ip="10.0.0.9"):
    scope = {
        "type": "http", "method": "GET", "path": "/", "query_string": query.encode(),
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
        "client": (client_ip, 1234),
    }
    return Request(scope)


def test_rate_limit_key_is_the_telegram_user_not_the_ip():
    init = urlencode({"user": json.dumps({"id": 424242}), "auth_date": "1", "hash": "x"})
    assert telegram_user_key(_request({"X-Telegram-Init-Data": init})) == "tg:424242"
    # two users behind one carrier-NAT IP get separate buckets
    other = urlencode({"user": json.dumps({"id": 7}), "hash": "x"})
    assert telegram_user_key(_request({"X-Telegram-Init-Data": other})) == "tg:7"
    # garbage / no identity -> falls back to the IP
    assert telegram_user_key(_request({"X-Telegram-Init-Data": "junk"})) == "10.0.0.9"
    assert telegram_user_key(_request()) == "10.0.0.9"


# ---------------------------------------------------------------------------
# tiers
# ---------------------------------------------------------------------------
async def test_settings_publish_tier_multipliers(client):
    tiers = (await client.get("/settings/")).json()["tiers"]
    assert {"name": "GOAT", "min_score": 1000, "multiplier": 1.35} in tiers
    assert tiers[-1] == {"name": "Anon", "min_score": 0, "multiplier": 1.0}


async def test_worker_startup_and_each_batch_load_retuned_tiers(db_session, make_user, monkeypatch):
    from app.models.verification_batch import VerificationBatch
    from app.services import claims
    from app.tasks import worker

    calls = []

    async def spy(db):
        calls.append(db)
        return True

    monkeypatch.setattr(tier, "load_tiers_from_settings", spy)

    class _Session:
        async def __aenter__(self):
            return "db"

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(worker, "SessionLocal", lambda: _Session())
    await worker.startup({})
    assert calls == ["db"]

    # run_batch reloads before paying out (an empty batch is enough)
    user = await make_user()
    batch = VerificationBatch(user_id=user.id, engagement_ids=[])
    db_session.add(batch)
    await db_session.commit()
    await claims.run_batch(db_session, batch.id)
    assert len(calls) == 2


async def test_tier_reload_follows_admin_settings(db_session):
    rows = [
        ("TIER_NORMIE_THRESHOLD", "50"), ("TIER_DEGEN_THRESHOLD", "100"), ("TIER_BASED_THRESHOLD", "200"),
        ("TIER_LEGEND_THRESHOLD", "300"), ("TIER_OG_THRESHOLD", "400"), ("TIER_GOAT_THRESHOLD", "500"),
    ]
    for key, value in rows:
        db_session.add(SiteSetting(key=key, value=value, data_type="int"))
    for name, mult in (("ANON", "1.00"), ("NORMIE", "1.10"), ("DEGEN", "1.15"), ("BASED", "1.20"),
                       ("LEGEND", "1.25"), ("OG", "1.30"), ("GOAT", "1.50")):
        db_session.add(SiteSetting(key=f"TIER_{name}_MULTIPLIER", value=mult, data_type="decimal"))
    await db_session.commit()
    site_settings._cache.clear()
    before = list(tier.TIERS)
    try:
        assert await tier.load_tiers_from_settings(db_session) is True
        assert tier.tier_for(587) == "GOAT"
        assert float(tier.multiplier_for(587)) == 1.5
    finally:
        tier.TIERS[:] = before


# ---------------------------------------------------------------------------
# identity: never credit another account's score / id
# ---------------------------------------------------------------------------
class _Provider:
    def __init__(self, data):
        self.data = data

    def is_unavailable(self):
        return False

    async def get_user_data(self, username):
        return self.data


async def test_signup_score_for_a_different_x_account_is_not_stored(db_session, monkeypatch):
    """Handle recycled: Sorsa still answers @alpha with the previous owner's
    account (id 999). The applicant OAuth-proved id 1201 — don't credit it."""
    entry = await _register(db_session, 201, "alpha", "1201")
    monkeypatch.setattr(scores_svc, "get_score_client",
                        lambda: _Provider({"id": "999", "screen_name": "alpha", "score": 4000}))
    assert await scores_svc.fetch_waitlist_score(db_session, entry.id) == "not_found"
    await db_session.refresh(entry)
    assert entry.score is None


async def test_refresh_never_overwrites_a_verified_users_oauth_id(client, db_session, make_user, monkeypatch):
    user = await make_user(telegram_id=211, x_username="beta", x_verified=True,
                           tweetscout_last_updated=utcnow() - timedelta(days=1))
    await XProfileRepository(db_session).create(user_id=user.id, x_user_id="1211", username="beta")
    await db_session.commit()
    monkeypatch.setattr(scores_svc, "get_score_client",
                        lambda: _Provider({"id": "999", "screen_name": "beta", "score": 4000}))
    body = (await client.post("/user/refresh-score/", params={"telegram_id": 211})).json()
    assert body["result"] == "not_found"
    profile = await XProfileRepository(db_session).get(user_id=user.id)
    assert profile.x_user_id == "1211"
    await db_session.refresh(user)
    assert user.tweetscout_score == 0


async def test_refresh_blocked_for_banned_and_rejected(client, db_session, make_user, monkeypatch):
    monkeypatch.setattr(scores_svc, "get_score_client", lambda: _Provider(None))
    await make_user(telegram_id=221, x_username="banned_one", is_banned=True)
    assert (await client.post("/user/refresh-score/", params={"telegram_id": 221})).status_code == 403
    admin = await make_user()
    entry = await _register(db_session, 222, "rejected_two", "1222")
    await svc.reject_entry(db_session, entry_id=entry.id, admin_id=admin.id)
    assert (await client.post("/user/refresh-score/", params={"telegram_id": 222})).status_code == 403


# ---------------------------------------------------------------------------
# forged credentials
# ---------------------------------------------------------------------------
def test_proofs_signed_with_the_dev_key_are_invalid_outside_debug(monkeypatch):
    monkeypatch.setattr(settings, "secret_key", "dev-insecure-secret-change-me")
    monkeypatch.setattr(settings, "debug", True)
    forged = sign_x_proof({"tg_id": 1, "x_username": "whale", "x_user_id": "1", "iat": 0})
    monkeypatch.setattr(settings, "debug", False)
    assert verify_x_proof(forged) is None


def test_empty_bot_token_never_verifies_init_data():
    with pytest.raises(ValueError):
        verify_init_data("user=%7B%22id%22%3A1%7D&auth_date=1&hash=abc", "")
