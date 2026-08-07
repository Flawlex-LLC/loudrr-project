"""Tests for Ch13 — two-phase verification + the claim queue (endpoints 12, 13).

The Twitter API is mocked. The settlement money paths are tested hardest:
award, tier multiplier, partial payment, failed-deletes-engagement,
benefit-of-the-doubt, daily-cap skip, and idempotent re-run.
"""
import uuid
from datetime import timedelta
from decimal import Decimal


from app.core.time_utils import utcnow
from app.integrations import twitter
from app.models.engagement import Engagement
from app.models.post import Post
from app.models.site_setting import SiteSetting
from app.models.verification_batch import VerificationBatch
from app.services import claims
from app.services import site_settings


class _FakeTwitter:
    def __init__(self, *, passed=True, skipped=False):
        self.passed, self.skipped = passed, skipped

    async def verify_reply(self, tweet_id, x_username, *, max_retries=0):
        if self.skipped:
            return {"passed": True, "reply_verified": True, "like_verified": True,
                    "error": None, "skipped": True}
        return {"passed": self.passed, "reply_verified": self.passed,
                "like_verified": True, "error": None if self.passed else "no reply",
                "skipped": False}


def _mock_twitter(monkeypatch, **kw):
    monkeypatch.setattr(twitter, "get_twitter_client", lambda: _FakeTwitter(**kw))


async def _make_post(db, *, owner_id, escrow="50", tweet_id="123", x_link="https://x.com/o/status/123", status="active"):
    p = Post(
        user_id=owner_id, x_link=x_link, tweet_id=tweet_id,
        escrow=Decimal(escrow), initial_escrow=Decimal("50"), status=status, platform="web",
    )
    db.add(p)
    await db.commit()
    return p


async def _make_engagement(db, *, user_id, post_id, clicked_at=None):
    e = Engagement(
        user_id=user_id, post_id=post_id, verified=False, credit_granted=False,
        clicked_at=clicked_at or utcnow(),
    )
    db.add(e)
    await db.commit()
    return e


async def _make_batch(db, *, user_id, engagement_ids):
    b = VerificationBatch(
        user_id=user_id, engagement_ids=[str(i) for i in engagement_ids], status="pending"
    )
    db.add(b)
    await db.commit()
    return b


# ============ settlement engine (run_batch) ============
async def test_award_happy_path(client, make_user, db_session, monkeypatch):
    owner = await make_user(telegram_id=8001)
    viewer = await make_user(telegram_id=8002, x_username="viewer")  # Anon, x1.00
    post = await _make_post(db_session, owner_id=owner.id, escrow="50")
    eng = await _make_engagement(db_session, user_id=viewer.id, post_id=post.id)
    batch = await _make_batch(db_session, user_id=viewer.id, engagement_ids=[eng.id])
    _mock_twitter(monkeypatch, passed=True)

    await claims.run_batch(db_session, batch.id)

    assert batch.status == "completed"
    assert (batch.passed, batch.failed) == (1, 0)
    assert batch.credits_awarded == Decimal("1.0000")
    assert post.escrow == Decimal("49.0000")
    assert viewer.credits == Decimal("1.0000")
    assert eng.verified is True and eng.credit_granted is True
    assert viewer.total_engagements == 1


async def test_award_uses_tier_multiplier(client, make_user, db_session, monkeypatch):
    owner = await make_user(telegram_id=8003)
    viewer = await make_user(telegram_id=8004, x_username="v", tweetscout_score=450)  # Based x1.20
    post = await _make_post(db_session, owner_id=owner.id, escrow="50")
    eng = await _make_engagement(db_session, user_id=viewer.id, post_id=post.id)
    batch = await _make_batch(db_session, user_id=viewer.id, engagement_ids=[eng.id])
    _mock_twitter(monkeypatch, passed=True)

    await claims.run_batch(db_session, batch.id)
    assert viewer.credits == Decimal("1.2000")
    assert post.escrow == Decimal("48.8000")


async def test_partial_payment_and_autocomplete(client, make_user, db_session, monkeypatch):
    owner = await make_user(telegram_id=8005)
    viewer = await make_user(telegram_id=8006, x_username="v", tweetscout_score=450)  # wants 1.20
    post = await _make_post(db_session, owner_id=owner.id, escrow="0.5")  # only 0.5 left
    eng = await _make_engagement(db_session, user_id=viewer.id, post_id=post.id)
    batch = await _make_batch(db_session, user_id=viewer.id, engagement_ids=[eng.id])
    _mock_twitter(monkeypatch, passed=True)

    await claims.run_batch(db_session, batch.id)
    assert viewer.credits == Decimal("0.5000")     # capped at escrow
    assert post.escrow == Decimal("0.0000")
    assert post.status == "completed"               # auto-completed at escrow 0
    assert batch.credits_awarded == Decimal("0.5000")


async def test_failed_verification_deletes_engagement(client, make_user, db_session, monkeypatch):
    from app.repositories.engagement import EngagementRepository

    owner = await make_user(telegram_id=8007)
    viewer = await make_user(telegram_id=8008, x_username="v")
    post = await _make_post(db_session, owner_id=owner.id, escrow="50")
    eng = await _make_engagement(db_session, user_id=viewer.id, post_id=post.id)
    eng_id = eng.id
    batch = await _make_batch(db_session, user_id=viewer.id, engagement_ids=[eng_id])
    _mock_twitter(monkeypatch, passed=False)

    await claims.run_batch(db_session, batch.id)
    assert (batch.passed, batch.failed) == (0, 1)
    assert batch.credits_awarded == Decimal("0")
    assert viewer.credits == Decimal("0")
    assert viewer.honesty_score == 49               # dropped by ceil(1/2)=1
    assert await EngagementRepository(db_session).get(id=eng_id) is None  # deleted


async def test_benefit_of_doubt_no_tweet_id(client, make_user, db_session, monkeypatch):
    owner = await make_user(telegram_id=8009)
    viewer = await make_user(telegram_id=8010, x_username="v")
    # x_link has no /status/ id and tweet_id empty → can't verify → passed (skipped)
    post = await _make_post(db_session, owner_id=owner.id, escrow="50", tweet_id="", x_link="https://x.com/owner")
    eng = await _make_engagement(db_session, user_id=viewer.id, post_id=post.id)
    batch = await _make_batch(db_session, user_id=viewer.id, engagement_ids=[eng.id])
    _mock_twitter(monkeypatch, passed=False)  # must NOT be consulted

    await claims.run_batch(db_session, batch.id)
    assert (batch.passed, batch.failed) == (1, 0)
    assert viewer.credits == Decimal("1.0000")


async def test_daily_cap_skips_award_preserving_escrow(client, make_user, db_session, monkeypatch):
    owner = await make_user(telegram_id=8011)
    # already at the daily cap (100) → no headroom
    viewer = await make_user(
        telegram_id=8012, x_username="v",
        daily_credits_earned=Decimal("100"), daily_earned_reset_at=utcnow(),
    )
    post = await _make_post(db_session, owner_id=owner.id, escrow="50")
    eng = await _make_engagement(db_session, user_id=viewer.id, post_id=post.id)
    batch = await _make_batch(db_session, user_id=viewer.id, engagement_ids=[eng.id])
    _mock_twitter(monkeypatch, passed=True)

    await claims.run_batch(db_session, batch.id)
    assert batch.passed == 1                  # passed verification
    assert batch.credits_awarded == Decimal("0")
    assert post.escrow == Decimal("50.0000")  # escrow preserved (skipped, not partial)
    assert eng.verified is True and eng.credit_granted is False


async def test_rerun_is_idempotent(client, make_user, db_session, monkeypatch):
    owner = await make_user(telegram_id=8013)
    viewer = await make_user(telegram_id=8014, x_username="v")
    post = await _make_post(db_session, owner_id=owner.id, escrow="50")
    eng = await _make_engagement(db_session, user_id=viewer.id, post_id=post.id)
    batch = await _make_batch(db_session, user_id=viewer.id, engagement_ids=[eng.id])
    _mock_twitter(monkeypatch, passed=True)

    await claims.run_batch(db_session, batch.id)
    second = await claims.run_batch(db_session, batch.id)
    assert second.get("already_processed") is True
    assert viewer.credits == Decimal("1.0000")  # not double-credited


async def test_multi_engagement_batch_mixed_outcomes(client, make_user, db_session, monkeypatch):
    """A batch of 3: two verify, one fails. Settlement is per-engagement — the
    two pass (escrow + credit move on each), the failure is deleted and docks
    honesty, and the failed post's escrow is left untouched."""
    from app.repositories.engagement import EngagementRepository

    owner = await make_user(telegram_id=8201)
    viewer = await make_user(telegram_id=8202, x_username="v")  # Anon x1.00
    p1 = await _make_post(db_session, owner_id=owner.id, escrow="50", tweet_id="t1", x_link="https://x.com/o/status/t1")
    p2 = await _make_post(db_session, owner_id=owner.id, escrow="50", tweet_id="t2", x_link="https://x.com/o/status/t2")
    p3 = await _make_post(db_session, owner_id=owner.id, escrow="50", tweet_id="t3", x_link="https://x.com/o/status/t3")
    e1 = await _make_engagement(db_session, user_id=viewer.id, post_id=p1.id)
    e2 = await _make_engagement(db_session, user_id=viewer.id, post_id=p2.id)
    e3 = await _make_engagement(db_session, user_id=viewer.id, post_id=p3.id)
    e3_id = e3.id

    class _PerTweet:
        async def verify_reply(self, tweet_id, x_username, *, max_retries=0):
            ok = tweet_id != "t3"  # only t3 fails
            return {"passed": ok, "reply_verified": ok, "like_verified": True,
                    "error": None if ok else "no reply", "skipped": False}

    monkeypatch.setattr(twitter, "get_twitter_client", lambda: _PerTweet())

    batch = await _make_batch(db_session, user_id=viewer.id, engagement_ids=[e1.id, e2.id, e3_id])
    await claims.run_batch(db_session, batch.id)

    assert (batch.passed, batch.failed) == (2, 1)
    assert batch.credits_awarded == Decimal("2.0000")     # 1.00 each for the two that passed
    assert viewer.credits == Decimal("2.0000")
    assert viewer.total_engagements == 2                  # only the awarded ones count
    assert viewer.honesty_score == 49                     # one failure → ceil(1/2)=1
    assert p1.escrow == Decimal("49.0000")                # each passed post paid 1
    assert p2.escrow == Decimal("49.0000")
    assert p3.escrow == Decimal("50.0000")                # failed → escrow untouched
    assert await EngagementRepository(db_session).get(id=e3_id) is None  # failed deleted


# ============ queue-claim gates (endpoint) ============
def _no_background(monkeypatch):
    async def _noop(batch_id):
        return None
    monkeypatch.setattr(claims, "process_batch_in_new_session", _noop)


def _seed(db, **kv):
    for k, v in kv.items():
        db.add(SiteSetting(key=k, value=str(v), data_type="int"))


async def test_queue_requires_x_username(client, make_user):
    await make_user(telegram_id=8015)  # no x_username
    r = await client.post("/session/queue-claim/", params={"telegram_id": 8015})
    assert r.status_code == 400
    assert r.json()["error"] == "x_account_required"


async def test_queue_not_enough_engagements(client, make_user, db_session):
    _seed(db_session, MIN_ENGAGEMENTS_TO_CLAIM=2)
    await db_session.commit()
    site_settings._cache.clear()
    await make_user(telegram_id=8016, x_username="v")
    r = await client.post("/session/queue-claim/", params={"telegram_id": 8016})
    assert r.status_code == 200
    assert r.json()["success"] is False
    assert r.json()["pending_count"] == 0


async def test_queue_duration_gate(client, make_user, db_session, monkeypatch):
    _seed(db_session, MIN_ENGAGEMENTS_TO_CLAIM=1, MIN_SESSION_DURATION_SECONDS=3600)
    owner = await make_user(telegram_id=8017)
    viewer = await make_user(telegram_id=8018, x_username="v")
    post = await _make_post(db_session, owner_id=owner.id)
    await _make_engagement(db_session, user_id=viewer.id, post_id=post.id)
    site_settings._cache.clear()
    r = await client.post("/session/queue-claim/", params={"telegram_id": 8018})
    body = r.json()
    assert body["success"] is False
    assert body["error"] == "insufficient_engagement_time"
    assert body["remaining_seconds"] > 0


async def test_queue_success_creates_batch(client, make_user, db_session, monkeypatch):
    _seed(db_session, MIN_ENGAGEMENTS_TO_CLAIM=1, MIN_SESSION_DURATION_SECONDS=0)
    owner = await make_user(telegram_id=8019)
    viewer = await make_user(telegram_id=8020, x_username="v")
    post = await _make_post(db_session, owner_id=owner.id)
    await _make_engagement(db_session, user_id=viewer.id, post_id=post.id)
    site_settings._cache.clear()
    _no_background(monkeypatch)  # don't actually process in this endpoint test

    r = await client.post("/session/queue-claim/", params={"telegram_id": 8020})
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["status"] == "pending"
    assert body["engagement_count"] == 1
    assert body["position"] == 1


# ============ AUDIT_PROBABILITY (live wiring) ============
# verify_engagements now reads AUDIT_PROBABILITY (default 1.0 = verify all).
# When set <1.0, items rolled above the threshold get a trusted-skip pass
# without ever hitting the Twitter API. We monkeypatch random.random() so the
# behavior is deterministic in the test.
async def test_audit_probability_skips_api_call(
    client, make_user, db_session, monkeypatch
):
    # set the setting low; force random() to return a value ABOVE it so the
    # item is trusted-skipped
    db_session.add(SiteSetting(
        key="AUDIT_PROBABILITY", value="0.05", data_type="float",
    ))
    await db_session.commit()
    site_settings._cache.clear()

    # twitter client should NOT be consulted on the trusted skip
    class _ExplodingTwitter:
        async def verify_reply(self, *a, **kw):
            raise AssertionError("Twitter API must not be called on trusted skip")

    monkeypatch.setattr(twitter, "get_twitter_client", lambda: _ExplodingTwitter())
    # random.random() returns 0.5 — above 0.05 — so the item is skipped
    from app.services import verification as verification_mod
    monkeypatch.setattr(verification_mod.random, "random", lambda: 0.5)

    owner = await make_user(telegram_id=8501)
    viewer = await make_user(telegram_id=8502, x_username="v")
    post = await _make_post(db_session, owner_id=owner.id, escrow="50")
    eng = await _make_engagement(db_session, user_id=viewer.id, post_id=post.id)
    batch = await _make_batch(db_session, user_id=viewer.id, engagement_ids=[eng.id])

    await claims.run_batch(db_session, batch.id)
    # trusted skip is recorded as passed — engagement is credited normally
    assert (batch.passed, batch.failed) == (1, 0)
    assert viewer.credits == Decimal("1.0000")


# ============ streak port: bump + bonus on settlement ============
async def test_settlement_bumps_streak_to_one_on_first_engagement(
    client, make_user, db_session, monkeypatch,
):
    """A user with no prior last_engagement_date starts at streak=1 after a
    successful settlement (one bump per batch, gated on awarded_count > 0)."""
    owner = await make_user(telegram_id=8601)
    viewer = await make_user(telegram_id=8602, x_username="v")
    post = await _make_post(db_session, owner_id=owner.id, escrow="50")
    eng = await _make_engagement(db_session, user_id=viewer.id, post_id=post.id)
    batch = await _make_batch(db_session, user_id=viewer.id, engagement_ids=[eng.id])
    _mock_twitter(monkeypatch, passed=True)

    await claims.run_batch(db_session, batch.id)
    await db_session.refresh(viewer)
    assert viewer.current_streak == 1
    assert viewer.longest_streak == 1
    assert viewer.last_engagement_date == utcnow().date()


async def test_settlement_no_award_no_streak_bump(
    client, make_user, db_session, monkeypatch,
):
    """All-failed batch (zero awards) must NOT bump the streak — the streak
    is gated on at least one settled award per the port-plan."""
    owner = await make_user(telegram_id=8603)
    viewer = await make_user(telegram_id=8604, x_username="v")
    post = await _make_post(db_session, owner_id=owner.id, escrow="50")
    eng = await _make_engagement(db_session, user_id=viewer.id, post_id=post.id)
    batch = await _make_batch(db_session, user_id=viewer.id, engagement_ids=[eng.id])
    _mock_twitter(monkeypatch, passed=False)

    await claims.run_batch(db_session, batch.id)
    await db_session.refresh(viewer)
    assert viewer.current_streak == 0
    assert viewer.last_engagement_date is None


async def test_settlement_at_streak_seven_pays_bonus(
    client, make_user, db_session, monkeypatch,
):
    """Settlement lands the streak on 7 — milestone bonus (default 5) is
    credited on top of the per-engagement karma, escrow only loses the
    per-engagement portion (the bonus is platform-funded)."""
    site_settings._cache.clear()
    owner = await make_user(telegram_id=8701)
    viewer = await make_user(
        telegram_id=8702, x_username="v",
        current_streak=6, longest_streak=6,
        last_engagement_date=utcnow().date() - timedelta(days=1),
    )
    post = await _make_post(db_session, owner_id=owner.id, escrow="50")
    eng = await _make_engagement(db_session, user_id=viewer.id, post_id=post.id)
    batch = await _make_batch(db_session, user_id=viewer.id, engagement_ids=[eng.id])
    _mock_twitter(monkeypatch, passed=True)

    await claims.run_batch(db_session, batch.id)
    await db_session.refresh(viewer)
    await db_session.refresh(post)
    assert viewer.current_streak == 7
    # 1 karma per engagement (Anon tier × 1.0 streak) + 5 milestone bonus
    assert viewer.credits == Decimal("6.0000")
    assert viewer.total_credits_earned == Decimal("6.0000")
    # escrow only debited the per-engagement karma; the bonus is platform-funded
    assert post.escrow == Decimal("49.0000")
    # batch.credits_awarded reflects the full delta the user just earned
    assert batch.credits_awarded == Decimal("6.0000")


async def test_settlement_streak_bonus_idempotent_on_rerun(
    client, make_user, db_session, monkeypatch,
):
    """Re-running the same batch (already_processed branch) must NOT re-pay
    the streak bonus — the bonus idempotency key is per (user, threshold)."""
    site_settings._cache.clear()
    owner = await make_user(telegram_id=8703)
    viewer = await make_user(
        telegram_id=8704, x_username="v",
        current_streak=6, longest_streak=6,
        last_engagement_date=utcnow().date() - timedelta(days=1),
    )
    post = await _make_post(db_session, owner_id=owner.id, escrow="50")
    eng = await _make_engagement(db_session, user_id=viewer.id, post_id=post.id)
    batch = await _make_batch(db_session, user_id=viewer.id, engagement_ids=[eng.id])
    _mock_twitter(monkeypatch, passed=True)

    await claims.run_batch(db_session, batch.id)
    second = await claims.run_batch(db_session, batch.id)
    assert second.get("already_processed") is True
    await db_session.refresh(viewer)
    # NOT double-paid: 1 karma + 5 bonus, exactly once
    assert viewer.credits == Decimal("6.0000")


async def test_settlement_streak_multiplier_compounds_with_tier(
    client, make_user, db_session, monkeypatch,
):
    """STREAK_7_DAY_MULTIPLIER=1.5 stacks on the tier multiplier inside
    karma_for. No-inflation invariant: escrow deducted == karma credited
    for the per-engagement award."""
    db_session.add(SiteSetting(
        key="STREAK_7_DAY_MULTIPLIER", value="1.5", data_type="decimal",
    ))
    await db_session.commit()
    site_settings._cache.clear()

    owner = await make_user(telegram_id=8801)
    viewer = await make_user(
        telegram_id=8802, x_username="v", tweetscout_score=450,  # Based × 1.20
        current_streak=7, longest_streak=7,  # already in the 7-day band
        last_engagement_date=utcnow().date(),  # same day → no further bump
    )
    post = await _make_post(db_session, owner_id=owner.id, escrow="50")
    eng = await _make_engagement(db_session, user_id=viewer.id, post_id=post.id)
    batch = await _make_batch(db_session, user_id=viewer.id, engagement_ids=[eng.id])
    _mock_twitter(monkeypatch, passed=True)

    await claims.run_batch(db_session, batch.id)
    await db_session.refresh(viewer)
    await db_session.refresh(post)
    # 1 * 1.20 (tier) * 1.5 (streak) = 1.8000
    assert viewer.credits == Decimal("1.8000")
    # no-inflation: escrow lost exactly what the user gained on the engagement
    assert post.escrow == Decimal("48.2000")
    # same UTC day → no streak bump, no milestone, no bonus
    assert viewer.current_streak == 7


async def test_audit_probability_default_verifies_everything(
    client, make_user, db_session, monkeypatch
):
    """No AUDIT_PROBABILITY row in site_settings → service-level default 1.0
    → every engagement gets the full Twitter check (current behavior)."""
    site_settings._cache.clear()
    calls = {"n": 0}

    class _CountingTwitter:
        async def verify_reply(self, *a, **kw):
            calls["n"] += 1
            return {"passed": True, "reply_verified": True, "like_verified": True,
                    "error": None, "skipped": False}

    monkeypatch.setattr(twitter, "get_twitter_client", lambda: _CountingTwitter())

    owner = await make_user(telegram_id=8503)
    viewer = await make_user(telegram_id=8504, x_username="v")
    post = await _make_post(db_session, owner_id=owner.id, escrow="50")
    eng = await _make_engagement(db_session, user_id=viewer.id, post_id=post.id)
    batch = await _make_batch(db_session, user_id=viewer.id, engagement_ids=[eng.id])

    await claims.run_batch(db_session, batch.id)
    assert calls["n"] == 1  # the API WAS called


# ============ VERIFICATION_BATCH_SIZE (live wiring) ============
async def test_verification_batch_size_caps_batch(
    client, make_user, db_session, monkeypatch
):
    """When VERIFICATION_BATCH_SIZE is set, only the oldest N pending
    engagements ride this batch; the rest stay queued for the next claim."""
    _seed(db_session, MIN_ENGAGEMENTS_TO_CLAIM=1, MIN_SESSION_DURATION_SECONDS=0)
    db_session.add(SiteSetting(
        key="VERIFICATION_BATCH_SIZE", value="2", data_type="int",
    ))
    await db_session.commit()
    site_settings._cache.clear()
    _no_background(monkeypatch)

    owner = await make_user(telegram_id=8505)
    viewer = await make_user(telegram_id=8506, x_username="v")
    # create 5 engagements; only the 2 oldest should enter this batch
    for i in range(5):
        p = await _make_post(
            db_session, owner_id=owner.id, escrow="50",
            tweet_id=str(100 + i),
            x_link=f"https://x.com/owner/status/{100 + i}",
        )
        await _make_engagement(db_session, user_id=viewer.id, post_id=p.id)

    r = await client.post("/session/queue-claim/", params={"telegram_id": 8506})
    body = r.json()
    assert body["success"] is True
    assert body["engagement_count"] == 2  # capped


# ============ VERIFICATION_SAMPLE_SIZE (live wiring) ============
async def test_verification_sample_size_caps_audits_per_batch(
    client, make_user, db_session, monkeypatch
):
    """SAMPLE_SIZE caps how many items in this batch hit the Twitter API.
    With AUDIT_PROBABILITY=1.0 (audit every roll) and SAMPLE_SIZE=1, exactly
    one of the three engagements gets the real API call; the other two are
    trusted-passed without consulting the Twitter client."""
    db_session.add(SiteSetting(
        key="AUDIT_PROBABILITY", value="1.0", data_type="float",
    ))
    db_session.add(SiteSetting(
        key="VERIFICATION_SAMPLE_SIZE", value="1", data_type="int",
    ))
    await db_session.commit()
    site_settings._cache.clear()

    calls = {"n": 0}

    class _CountingTwitter:
        async def verify_reply(self, *a, **kw):
            calls["n"] += 1
            return {"passed": True, "reply_verified": True, "like_verified": True,
                    "error": None, "skipped": False}

    monkeypatch.setattr(twitter, "get_twitter_client", lambda: _CountingTwitter())

    owner = await make_user(telegram_id=8601)
    viewer = await make_user(telegram_id=8602, x_username="v")
    eng_ids = []
    for i in range(3):
        p = await _make_post(
            db_session, owner_id=owner.id, escrow="50",
            tweet_id=str(200 + i),
            x_link=f"https://x.com/owner/status/{200 + i}",
        )
        e = await _make_engagement(db_session, user_id=viewer.id, post_id=p.id)
        eng_ids.append(e.id)
    batch = await _make_batch(db_session, user_id=viewer.id, engagement_ids=eng_ids)

    await claims.run_batch(db_session, batch.id)

    assert calls["n"] == 1                          # only ONE API call
    assert (batch.passed, batch.failed) == (3, 0)   # but all 3 counted as passed
    assert viewer.credits == Decimal("3.0000")


async def test_verification_sample_size_zero_means_no_cap(
    client, make_user, db_session, monkeypatch
):
    """SAMPLE_SIZE=0 (and missing) is the 'no cap' sentinel — every item the
    AUDIT_PROBABILITY roll picks gets the real API call."""
    db_session.add(SiteSetting(
        key="AUDIT_PROBABILITY", value="1.0", data_type="float",
    ))
    db_session.add(SiteSetting(
        key="VERIFICATION_SAMPLE_SIZE", value="0", data_type="int",
    ))
    await db_session.commit()
    site_settings._cache.clear()

    calls = {"n": 0}

    class _CountingTwitter:
        async def verify_reply(self, *a, **kw):
            calls["n"] += 1
            return {"passed": True, "reply_verified": True, "like_verified": True,
                    "error": None, "skipped": False}

    monkeypatch.setattr(twitter, "get_twitter_client", lambda: _CountingTwitter())

    owner = await make_user(telegram_id=8603)
    viewer = await make_user(telegram_id=8604, x_username="v")
    eng_ids = []
    for i in range(3):
        p = await _make_post(
            db_session, owner_id=owner.id, escrow="50",
            tweet_id=str(300 + i),
            x_link=f"https://x.com/owner/status/{300 + i}",
        )
        e = await _make_engagement(db_session, user_id=viewer.id, post_id=p.id)
        eng_ids.append(e.id)
    batch = await _make_batch(db_session, user_id=viewer.id, engagement_ids=eng_ids)

    await claims.run_batch(db_session, batch.id)
    assert calls["n"] == 3  # every item got the API call


# ============ MAX_VERIFICATION_RETRIES (live wiring) ============
async def test_max_verification_retries_passed_through_to_client(
    client, make_user, db_session, monkeypatch
):
    """The setting value is read from site_settings and forwarded to the
    TwitterClient.verify_reply call via the max_retries kwarg. Bumping the
    setting in the DB and clearing the cache must make the next batch see the
    new value (proves the read is live, not hardcoded)."""
    db_session.add(SiteSetting(
        key="MAX_VERIFICATION_RETRIES", value="4", data_type="int",
    ))
    await db_session.commit()
    site_settings._cache.clear()

    seen: dict = {}

    class _RecordingTwitter:
        async def verify_reply(self, tweet_id, x_username, *, max_retries=0):
            seen["max_retries"] = max_retries
            return {"passed": True, "reply_verified": True, "like_verified": True,
                    "error": None, "skipped": False}

    monkeypatch.setattr(twitter, "get_twitter_client", lambda: _RecordingTwitter())

    owner = await make_user(telegram_id=8701)
    viewer = await make_user(telegram_id=8702, x_username="v")
    post = await _make_post(db_session, owner_id=owner.id, escrow="50")
    eng = await _make_engagement(db_session, user_id=viewer.id, post_id=post.id)
    batch = await _make_batch(db_session, user_id=viewer.id, engagement_ids=[eng.id])

    await claims.run_batch(db_session, batch.id)
    assert seen["max_retries"] == 4


async def test_twitter_client_retries_on_5xx_then_succeeds(monkeypatch):
    """Direct test of the retry wrapper in TwitterClient.verify_reply:
    a transient 500 on the first attempt followed by a 200 returns the
    success result (not the benefit-of-doubt skip)."""
    import httpx as _httpx

    from app.integrations import twitter as tw
    from app.integrations.twitter import TwitterClient

    class _Resp:
        def __init__(self, status, data=None, text=""):
            self.status_code = status
            self._data = data or {}
            self.text = text

        def json(self):
            return self._data

        def raise_for_status(self):
            if self.status_code >= 400:
                req = _httpx.Request("GET", "https://x")
                raise _httpx.HTTPStatusError(
                    f"{self.status_code}", request=req, response=self,
                )

    calls = {"n": 0}

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, headers=None, params=None):
            calls["n"] += 1
            if calls["n"] == 1:
                return _Resp(500, text="upstream")
            return _Resp(200, data={"tweets": [{"id": "9"}]})

    monkeypatch.setattr(tw.httpx, "AsyncClient", lambda *a, **kw: _Client())

    c = TwitterClient(api_key="key")
    result = await c.verify_reply("123", "alice", max_retries=2)
    assert calls["n"] == 2
    assert result["passed"] is True
    assert result["skipped"] is False
    assert result["error"] is None


async def test_twitter_client_does_not_retry_on_4xx(monkeypatch):
    """4xx is non-transient — returns benefit-of-doubt without retrying."""
    import httpx as _httpx

    from app.integrations import twitter as tw
    from app.integrations.twitter import TwitterClient

    class _Resp:
        def __init__(self, status, text=""):
            self.status_code = status
            self.text = text

        def json(self):
            return {}

        def raise_for_status(self):
            req = _httpx.Request("GET", "https://x")
            raise _httpx.HTTPStatusError(
                f"{self.status_code}", request=req, response=self,
            )

    calls = {"n": 0}

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, headers=None, params=None):
            calls["n"] += 1
            return _Resp(404, text="not found")

    monkeypatch.setattr(tw.httpx, "AsyncClient", lambda *a, **kw: _Client())

    c = TwitterClient(api_key="key")
    result = await c.verify_reply("123", "alice", max_retries=5)
    assert calls["n"] == 1                # NOT retried
    assert result["passed"] is True       # benefit of the doubt
    assert result["skipped"] is True
    assert "404" in result["error"]


# ============ claim history (endpoint) ============
async def test_claim_history_lists_batches(client, make_user, db_session):
    viewer = await make_user(telegram_id=8021, x_username="v")
    await _make_batch(db_session, user_id=viewer.id, engagement_ids=[uuid.uuid4()])

    r = await client.get("/claims/history/", params={"telegram_id": 8021})
    assert r.status_code == 200
    body = r.json()
    assert len(body["batches"]) == 1
    assert body["batches"][0]["status"] == "pending"
    assert body["has_processing"] is True
