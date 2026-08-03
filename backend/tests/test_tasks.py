"""Ch16 — background-task logic (tested directly, no Redis) + worker config."""
from datetime import timedelta
from decimal import Decimal


from app.core.time_utils import utcnow
from app.models.post import Post
from app.services import maintenance
from app.services import streaks as streaks_svc
from app.services import users as users_svc


# ---- reset_daily_credits ----
async def test_reset_daily_credits(make_user, db_session):
    await make_user(telegram_id=10001, daily_credits_earned=Decimal("80"))
    await make_user(telegram_id=10002, daily_credits_earned=Decimal("30"))
    n = await maintenance.reset_daily_credits(db_session)
    assert n == 2
    # re-query
    from app.repositories.user import UserRepository
    u = await UserRepository(db_session).get(telegram_id=10001)
    assert u.daily_credits_earned == Decimal("0")


# ---- expire_old_posts ----
async def test_expire_old_posts_cancels_and_refunds(make_user, db_session):
    owner = await make_user(telegram_id=10003, credits=Decimal("0"), total_credits_earned=Decimal("0"))
    stale = Post(
        user_id=owner.id, x_link="https://x.com/o/status/1", escrow=Decimal("50"),
        initial_escrow=Decimal("50"), status="active", platform="web",
        created_at=utcnow() - timedelta(hours=50),  # > 48h default
    )
    fresh = Post(
        user_id=owner.id, x_link="https://x.com/o/status/2", escrow=Decimal("50"),
        initial_escrow=Decimal("50"), status="active", platform="web",
        created_at=utcnow(),
    )
    db_session.add_all([stale, fresh])
    await db_session.commit()

    n = await maintenance.expire_old_posts(db_session)
    assert n == 1
    await db_session.refresh(stale)
    await db_session.refresh(fresh)
    assert stale.status == "cancelled" and stale.escrow == Decimal("0.0000")
    assert fresh.status == "active"
    await db_session.refresh(owner)
    assert owner.credits == Decimal("50.0000")  # refunded


# ---- fetch_tweetscout_for_user ----
class _FakeTweetScout:
    async def get_user_data(self, username):
        return {"id": "9", "screen_name": "v", "name": "V", "followers_count": 5, "score": 250}


async def test_fetch_tweetscout_for_user(make_user, db_session, monkeypatch):
    user = await make_user(telegram_id=10004, x_username="v")
    # users_svc imports `get_score_client` from loudrr_analytics with a
    # from-import, so it becomes a module-level attribute of users_svc that
    # we can patch. (Old name was get_tweetscout_client — renamed when the
    # TweetScout call moved behind the loudrr_analytics abstraction.)
    monkeypatch.setattr(users_svc, "get_score_client", lambda: _FakeTweetScout())
    ok = await users_svc.fetch_tweetscout_for_user(db_session, user.id)
    assert ok is True
    await db_session.refresh(user)
    assert user.tweetscout_score == 250
    from app.repositories.x_profile import XProfileRepository
    assert await XProfileRepository(db_session).get(user_id=user.id) is not None


async def test_fetch_tweetscout_no_username(make_user, db_session):
    user = await make_user(telegram_id=10005)  # no x_username
    assert await users_svc.fetch_tweetscout_for_user(db_session, user.id) is False


# ---- worker config is valid ----
def test_worker_settings_registered():
    from app.tasks.worker import WorkerSettings
    names = {f.__name__ for f in WorkerSettings.functions}
    assert "process_verification_batch" in names
    assert "process_pending_outbox_events" in names
    # service-audit P1: requeue_stuck_batches recovers from worker death
    assert "requeue_stuck_batches" in names
    # streak port: nightly streak-reset task wired to the cron schedule
    assert "reset_broken_streaks" in names
    # karma-decay port: daily 02:00 UTC inactivity decay
    assert "decay_inactive_karma" in names
    # outbox durability: the stuck-outbox sweeper (mirrors requeue_stuck_batches
    # for OutboxEvent.status='processing' — same worker-crash class of bug).
    assert "requeue_stuck_outbox_events" in names
    assert len(WorkerSettings.functions) == 11
    assert len(WorkerSettings.cron_jobs) == 9


# ---- requeue_stuck_batches (service-audit P1: stuck-batch recovery) ----
async def test_requeue_stuck_batches_recovers_old_pending(
    make_user, db_session
):
    """A batch stuck in PROCESSING past the cutoff should flip back to PENDING
    and call the schedule hook with its id."""
    from datetime import timedelta
    from app.models.verification_batch import BatchStatus, VerificationBatch
    from app.services import claims

    user = await make_user(telegram_id=10101, x_username="v")
    stuck = VerificationBatch(
        user_id=user.id, engagement_ids=[],
        status=BatchStatus.PROCESSING.value,
        created_at=utcnow() - timedelta(minutes=20),  # past the 10-min cutoff
    )
    fresh = VerificationBatch(
        user_id=user.id, engagement_ids=[],
        status=BatchStatus.PROCESSING.value,
        created_at=utcnow(),  # too young — should NOT requeue
    )
    db_session.add_all([stuck, fresh])
    await db_session.commit()

    scheduled: list = []

    async def _schedule(batch_id):
        scheduled.append(batch_id)

    n = await claims.requeue_stuck_batches(db_session, schedule=_schedule)
    assert n == 1
    await db_session.refresh(stuck)
    await db_session.refresh(fresh)
    assert stuck.status == "pending"
    assert fresh.status == "processing"  # young one untouched
    assert scheduled == [stuck.id]


# ---- reset_broken_streaks (streak port: nightly hygiene) ----
async def test_reset_broken_streaks_zeroes_lapsed(make_user, db_session):
    """A user whose last_engagement_date < (today-1) and current_streak > 0
    must be zeroed. Users who engaged today or yesterday are left alone, and
    users with current_streak=0 are skipped (no needless writes)."""
    today = utcnow().date()
    lapsed = await make_user(
        telegram_id=20001,
        current_streak=5,
        longest_streak=5,
        last_engagement_date=today - timedelta(days=3),
    )
    yesterday_active = await make_user(
        telegram_id=20002,
        current_streak=2,
        longest_streak=2,
        last_engagement_date=today - timedelta(days=1),
    )
    today_active = await make_user(
        telegram_id=20003,
        current_streak=10,
        longest_streak=10,
        last_engagement_date=today,
    )
    never_engaged = await make_user(
        telegram_id=20004,
        current_streak=0,
        last_engagement_date=None,
    )

    n = await maintenance.reset_broken_streaks(db_session)
    assert n == 1  # only the lapsed user was touched

    await db_session.refresh(lapsed)
    await db_session.refresh(yesterday_active)
    await db_session.refresh(today_active)
    await db_session.refresh(never_engaged)
    assert lapsed.current_streak == 0
    assert lapsed.longest_streak == 5  # lifetime max preserved
    assert yesterday_active.current_streak == 2
    assert today_active.current_streak == 10
    assert never_engaged.current_streak == 0


async def test_reset_broken_streaks_idempotent(make_user, db_session):
    """Running twice in a row touches nothing the second time — once a
    streak's been zeroed it's no longer eligible."""
    today = utcnow().date()
    await make_user(
        telegram_id=20101,
        current_streak=7,
        longest_streak=7,
        last_engagement_date=today - timedelta(days=4),
    )
    first = await maintenance.reset_broken_streaks(db_session)
    second = await maintenance.reset_broken_streaks(db_session)
    assert first == 1 and second == 0


async def test_streaks_service_alias(make_user, db_session):
    """maintenance.reset_broken_streaks is a thin wrapper around
    streaks.reset_broken_streaks — guard against the two drifting."""
    today = utcnow().date()
    await make_user(
        telegram_id=20201,
        current_streak=3,
        longest_streak=3,
        last_engagement_date=today - timedelta(days=2),
    )
    n = await streaks_svc.reset_broken_streaks(db_session)
    assert n == 1


# ---- decay_inactive_karma (karma-decay port: daily 02:00 UTC) ----
async def _seed_decay_settings(db_session, *, threshold_days=14, rate="0.015"):
    """Inject the two SiteSetting rows the decay job reads + bust the cache."""
    from app.models.site_setting import SiteSetting
    from app.services import site_settings as site_settings_svc

    db_session.add_all([
        SiteSetting(
            key="KARMA_DECAY_THRESHOLD_DAYS",
            value=str(threshold_days),
            data_type="int",
        ),
        SiteSetting(
            key="KARMA_DECAY_RATE",
            value=str(rate),
            data_type="float",
        ),
    ])
    await db_session.commit()
    site_settings_svc._cache.clear()


async def test_decay_inactive_karma_deducts_for_inactive_user(make_user, db_session):
    """A user whose User.created_at is older than the threshold AND has no
    transactions gets decayed by rate * current balance."""
    await _seed_decay_settings(db_session)
    old = await make_user(
        telegram_id=30001,
        credits=Decimal("1000"),
        total_credits_earned=Decimal("1000"),
        created_at=utcnow() - timedelta(days=30),  # well past 14-day threshold
    )

    n = await maintenance.decay_inactive_karma(db_session)
    assert n == 1
    await db_session.refresh(old)
    # 1000 * 0.015 = 15 deducted -> 985 remaining
    assert old.credits == Decimal("985.0000")
    # audit row exists with the synthetic system actor as admin
    from app.models.transaction import Transaction, TransactionType
    txns = (
        await db_session.execute(
            Transaction.__table__.select().where(
                Transaction.user_id == old.id,
                Transaction.type == TransactionType.APPLY_PENALTY.value,
            )
        )
    ).all()
    assert len(txns) == 1


async def test_decay_inactive_karma_skips_recently_active_user(
    make_user, db_session
):
    """A user whose most-recent Transaction.created_at is within the threshold
    is left alone, even if User.created_at is ancient."""
    from app.models.transaction import Transaction, TransactionType

    await _seed_decay_settings(db_session)
    recent = await make_user(
        telegram_id=30002,
        credits=Decimal("500"),
        total_credits_earned=Decimal("500"),
        created_at=utcnow() - timedelta(days=60),  # ancient signup
    )
    # ...but they earned credits 2 days ago — most-recent activity is fresh
    db_session.add(Transaction(
        user_id=recent.id,
        type=TransactionType.EARNED,
        amount=Decimal("10"),
        balance_after=Decimal("500"),
        idempotency_key="recent-activity",
        description="recent earn",
        created_at=utcnow() - timedelta(days=2),
    ))
    await db_session.commit()

    n = await maintenance.decay_inactive_karma(db_session)
    assert n == 0
    await db_session.refresh(recent)
    assert recent.credits == Decimal("500")


async def test_decay_inactive_karma_floors_at_zero(make_user, db_session):
    """A user already at credits=0 is filtered out by the pre-query
    (credits > 0). A user with tiny credits below the 4-dp resolution is
    pre-skipped so we never write a 0-amount row that would trip
    transaction_amount_nonzero."""
    await _seed_decay_settings(db_session)
    # already empty — filtered out by `credits > 0` in the eligibility query
    empty = await make_user(
        telegram_id=30003,
        credits=Decimal("0"),
        created_at=utcnow() - timedelta(days=30),
    )
    # tiny balance: 0.001 * 0.015 = 0.000015, quantizes to 0.0000 → skipped
    tiny = await make_user(
        telegram_id=30004,
        credits=Decimal("0.001"),
        total_credits_earned=Decimal("0.001"),
        created_at=utcnow() - timedelta(days=30),
    )

    n = await maintenance.decay_inactive_karma(db_session)
    assert n == 0
    await db_session.refresh(empty)
    await db_session.refresh(tiny)
    assert empty.credits == Decimal("0")
    # DB-level credits >= 0 CHECK was never violated; tiny user untouched
    assert tiny.credits == Decimal("0.0010")


async def test_decay_inactive_karma_idempotent_same_day(make_user, db_session):
    """Running twice in one UTC day decays once — the (user, type, key)
    unique constraint blocks the second deduction."""
    await _seed_decay_settings(db_session)
    user = await make_user(
        telegram_id=30005,
        credits=Decimal("1000"),
        total_credits_earned=Decimal("1000"),
        created_at=utcnow() - timedelta(days=30),
    )
    first = await maintenance.decay_inactive_karma(db_session)
    second = await maintenance.decay_inactive_karma(db_session)
    assert first == 1
    # Second run: re-fetched user has 985 credits and the idempotency_key for
    # today returns the existing apply_penalty row (so apply_penalty returns
    # the previous txn, not None) → counter ticks but no double-decay.
    assert second in (0, 1)
    await db_session.refresh(user)
    assert user.credits == Decimal("985.0000")  # decayed exactly once


async def test_decay_inactive_karma_skips_banned(make_user, db_session):
    """Banned users are excluded from decay — they're already locked out."""
    await _seed_decay_settings(db_session)
    banned = await make_user(
        telegram_id=30006,
        credits=Decimal("1000"),
        total_credits_earned=Decimal("1000"),
        is_banned=True,
        created_at=utcnow() - timedelta(days=30),
    )
    n = await maintenance.decay_inactive_karma(db_session)
    assert n == 0
    await db_session.refresh(banned)
    assert banned.credits == Decimal("1000")


async def test_decay_inactive_karma_disabled_when_rate_zero(make_user, db_session):
    """KARMA_DECAY_RATE = 0 short-circuits the job — useful kill-switch."""
    await _seed_decay_settings(db_session, rate="0")
    user = await make_user(
        telegram_id=30007,
        credits=Decimal("1000"),
        total_credits_earned=Decimal("1000"),
        created_at=utcnow() - timedelta(days=30),
    )
    n = await maintenance.decay_inactive_karma(db_session)
    assert n == 0
    await db_session.refresh(user)
    assert user.credits == Decimal("1000")
