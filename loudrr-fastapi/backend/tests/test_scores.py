"""Scores are fetched at exactly two moments — no scheduler:

  * sign-up: the fetch_waitlist_score job stores the result on the entry;
  * the mini-app Refresh button: POST /user/refresh-score/, per-account
    cooldown (SCORE_REFRESH_COOLDOWN_MINUTES).

The provider is always a fake; nothing here touches the network.
"""
from datetime import timedelta

from app.core.time_utils import utcnow
from app.models.site_setting import SiteSetting
from app.models.waitlist_entry import WaitlistEntry
from app.repositories.x_profile import XProfileRepository
from app.services import scores as scores_svc
from app.services import site_settings


def _payload(handle="applicant", score=587.2):
    return {
        "id": "1456366493323644928", "screen_name": handle, "name": "Blest",
        "score": score, "followers_count": 9251, "smart_followers": 618,
        "top_followers": [{"username": "notthreadguy"}, {"username": "MarioNawfal"}],
    }


class _Provider:
    def __init__(self, data=None, blocked=False):
        self.data = data
        self.blocked = blocked
        self.calls: list[str] = []

    def is_blocked(self):
        return self.blocked

    async def get_user_data(self, username):
        self.calls.append(username)
        return self.data


def _use(monkeypatch, provider):
    monkeypatch.setattr(scores_svc, "get_score_client", lambda: provider)
    return provider


async def _entry(db_session, telegram_id=7800, **fields):
    entry = WaitlistEntry(
        telegram_id=telegram_id, x_username="applicant",
        referral_code=f"SC{telegram_id:08d}", **fields,
    )
    db_session.add(entry)
    await db_session.commit()
    return entry


async def _cooldown(db_session, minutes):
    db_session.add(SiteSetting(key="SCORE_REFRESH_COOLDOWN_MINUTES", value=str(minutes), data_type="int"))
    await db_session.commit()
    site_settings._cache.clear()


# ---------------------------------------------------------------------------
# sign-up job
# ---------------------------------------------------------------------------
async def test_signup_fetch_stores_score_on_entry(db_session, monkeypatch):
    entry = await _entry(db_session)
    provider = _use(monkeypatch, _Provider(_payload()))

    assert await scores_svc.fetch_waitlist_score(db_session, str(entry.id)) == "found"

    await db_session.refresh(entry)
    assert provider.calls == ["applicant"]
    assert entry.score == 587.2
    assert entry.score_data["smart_followers"] == 618
    assert entry.score_updated_at is not None


async def test_signup_fetch_not_indexed_is_recorded_without_score(db_session, monkeypatch):
    entry = await _entry(db_session)
    _use(monkeypatch, _Provider(None))
    assert await scores_svc.fetch_waitlist_score(db_session, entry.id) == "not_found"
    await db_session.refresh(entry)
    assert entry.score is None
    assert entry.score_updated_at is not None  # card shows "not_found", not "pending"


async def test_signup_fetch_while_provider_blocked_stays_pending(db_session, monkeypatch):
    entry = await _entry(db_session)
    _use(monkeypatch, _Provider(None, blocked=True))
    assert await scores_svc.fetch_waitlist_score(db_session, entry.id) == "unavailable"
    await db_session.refresh(entry)
    # our outage must not start the applicant's Refresh cooldown
    assert entry.score_updated_at is None


class _TimingOutProvider(_Provider):
    """Live e2e regression: both proxies timed out, the provider returned None
    WITHOUT its circuit being open, and the sign-up job recorded "no score"."""

    def is_unavailable(self):
        return True


async def test_signup_fetch_proxy_timeouts_are_not_recorded_as_no_score(db_session, monkeypatch):
    entry = await _entry(db_session)
    _use(monkeypatch, _TimingOutProvider(None))
    assert await scores_svc.fetch_waitlist_score(db_session, entry.id) == "unavailable"
    await db_session.refresh(entry)
    assert entry.score is None and entry.score_updated_at is None  # still "pending"


async def test_signup_fetch_skips_an_entry_already_scored(db_session, monkeypatch):
    """A retried sign-up job must not overwrite a Refresh tap that landed first."""
    entry = await _entry(db_session, score=300.0, score_updated_at=utcnow())
    provider = _use(monkeypatch, _Provider(_payload(score=999)))
    assert await scores_svc.fetch_waitlist_score(db_session, entry.id) == "skipped"
    assert provider.calls == []


async def test_worker_retries_the_signup_fetch_only_while_unavailable(monkeypatch):
    import pytest
    from arq import Retry

    from app.tasks import worker

    outcomes = iter(["unavailable", "unavailable", "unavailable", "unavailable", "found"])

    async def fake_fetch(db, entry_id):
        return next(outcomes)

    class _NoSession:
        async def __aenter__(self):
            return None

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(worker.scores, "fetch_waitlist_score", fake_fetch)
    monkeypatch.setattr(worker, "SessionLocal", lambda: _NoSession())

    for attempt, delay in enumerate(worker.SIGNUP_SCORE_RETRY_DELAYS_S, start=1):
        with pytest.raises(Retry) as retry:
            await worker.fetch_waitlist_score({"job_try": attempt}, "entry-id")
        assert retry.value.defer_score == delay * 1000
    # out of retries: give up quietly — the card stays "pending", Refresh works
    assert await worker.fetch_waitlist_score({"job_try": 4}, "entry-id") == "unavailable"
    assert await worker.fetch_waitlist_score({"job_try": 1}, "entry-id") == "found"


# ---------------------------------------------------------------------------
# POST /user/refresh-score/
# ---------------------------------------------------------------------------
async def test_refresh_for_applicant_updates_entry(client, db_session, monkeypatch):
    entry = await _entry(db_session)
    _use(monkeypatch, _Provider(_payload()))

    r = await client.post("/user/refresh-score/", params={"telegram_id": 7800})

    assert r.status_code == 200
    body = r.json()
    assert body["result"] == "updated"
    assert body["x_username"] == "applicant"
    assert body["score"] == 587.2
    assert body["tier"] == "Based"
    assert body["followers"] == ["notthreadguy", "MarioNawfal"]
    assert body["followers_count"] == 618
    assert body["score_status"] == "ready"
    assert body["retry_after_seconds"] == 0
    await db_session.refresh(entry)
    assert entry.score == 587.2


async def test_refresh_for_approved_user_updates_user_and_profile(client, make_user, db_session, monkeypatch):
    user = await make_user(telegram_id=7801, x_username="0xBlest_", tweetscout_score=100,
                           tweetscout_last_updated=utcnow() - timedelta(days=2))
    _use(monkeypatch, _Provider(_payload("0xBlest_", 650)))

    body = (await client.post("/user/refresh-score/", params={"telegram_id": 7801})).json()

    assert body["result"] == "updated"
    assert body["score"] == 650 and body["tier"] == "Legend"
    await db_session.refresh(user)
    assert user.tweetscout_score == 650
    assert user.tweetscout_last_updated > utcnow() - timedelta(minutes=1)
    profile = await XProfileRepository(db_session).get(user_id=user.id)
    assert profile.score == 650


async def test_refresh_is_rate_limited_per_account(client, db_session, monkeypatch):
    await _entry(db_session, score=300.0, score_updated_at=utcnow() - timedelta(minutes=10))
    provider = _use(monkeypatch, _Provider(_payload()))

    body = (await client.post("/user/refresh-score/", params={"telegram_id": 7800})).json()

    assert body["result"] == "cooldown"
    assert provider.calls == []                        # no fetch
    assert 49 * 60 < body["retry_after_seconds"] <= 50 * 60 + 1  # 60-min default
    assert body["score"] == 300.0                      # the stored card comes back


async def test_refresh_cooldown_is_an_admin_setting(client, db_session, monkeypatch):
    await _entry(db_session, score=300.0, score_updated_at=utcnow() - timedelta(minutes=10))
    await _cooldown(db_session, 5)
    provider = _use(monkeypatch, _Provider(_payload()))
    body = (await client.post("/user/refresh-score/", params={"telegram_id": 7800})).json()
    assert body["result"] == "updated"
    assert provider.calls == ["applicant"]


async def test_refresh_not_found_keeps_old_score(client, db_session, monkeypatch):
    entry = await _entry(db_session, score=300.0, score_data=_payload(score=300.0),
                         score_updated_at=utcnow() - timedelta(days=1))
    _use(monkeypatch, _Provider(None))
    body = (await client.post("/user/refresh-score/", params={"telegram_id": 7800})).json()
    assert body["result"] == "not_found"
    assert body["score"] == 300.0
    await db_session.refresh(entry)
    assert entry.score_updated_at > utcnow() - timedelta(minutes=1)


async def test_refresh_while_provider_blocked_records_nothing(client, db_session, monkeypatch):
    entry = await _entry(db_session)
    _use(monkeypatch, _Provider(None, blocked=True))
    body = (await client.post("/user/refresh-score/", params={"telegram_id": 7800})).json()
    assert body["result"] == "unavailable"
    assert body["score_status"] == "pending"
    await db_session.refresh(entry)
    assert entry.score_updated_at is None  # no cooldown started


async def test_refresh_needs_an_x_account(client, make_user, monkeypatch):
    provider = _use(monkeypatch, _Provider(_payload()))
    await make_user(telegram_id=7802, x_username=None)
    r = await client.post("/user/refresh-score/", params={"telegram_id": 7802})
    assert r.status_code == 400
    r = await client.post("/user/refresh-score/", params={"telegram_id": 999999})  # stranger
    assert r.status_code == 400
    assert provider.calls == []


async def test_refresh_requires_identity(client):
    assert (await client.post("/user/refresh-score/")).status_code == 401


# ---------------------------------------------------------------------------
# admin: approvals aren't blind
# ---------------------------------------------------------------------------
async def test_admin_pending_waitlist_shows_the_stored_score(client, make_user, db_session):
    admin = await make_user(role="admin")
    await _entry(db_session, score=587.2, score_data=_payload(), score_updated_at=utcnow())
    await _entry(db_session, telegram_id=7803)  # not fetched yet

    rows = (await client.get("/api/admin/waitlist/pending/", params={"telegram_id": admin.telegram_id})).json()
    by_tg = {row["telegram_id"]: row for row in rows}
    assert by_tg[7800]["score"] == 587.2
    assert by_tg[7800]["tier"] == "Based"
    assert by_tg[7800]["smart_followers"] == 618
    assert by_tg[7803]["score"] is None and by_tg[7803]["score_updated_at"] is None
