"""Property-based tests (Hypothesis) — invariants that must hold for ALL inputs,
not just the handful we'd pick by hand.

Mirrors the property suite in the Django reference
(`../../loudrr/backend/core/tests/test_*_hypothesis.py`). Hypothesis generates
hundreds of inputs per property and, on failure, shrinks to the smallest
breaking example.

Two layers:
  * pure functions (tier math, karma, X-URL parsing, HMAC) — fast, no DB.
  * the credit ledger — drives the real CreditService against Postgres to prove
    no random earn/spend/penalty sequence can corrupt a balance.
"""
import asyncio
import hashlib
import hmac
import json
import re
import time
import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings as app_settings
from app.core.telegram_auth import verify_init_data
from app.db.base import Base
import app.models  # noqa: F401 — register every table on Base.metadata
from app.models.site_setting import SiteSetting
from app.models.user import User
from app.services import site_settings, tier
from app.services.credits import (
    CreditService, DailyCapReachedError, InsufficientCreditsError,
)
from app.services.x_url import extract_x_username

TEST_DATABASE_URL = app_settings.database_url.rsplit("/", 1)[0] + "/loudrr_test"

_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")
_MULTIPLIERS = {
    Decimal("1.00"), Decimal("1.10"), Decimal("1.15"), Decimal("1.20"),
    Decimal("1.25"), Decimal("1.30"), Decimal("1.35"),
}
_TIERS = {"Anon", "Normie", "Degen", "Based", "Legend", "OG", "GOAT"}

scores = st.floats(min_value=0, max_value=5000, allow_nan=False, allow_infinity=False)


# ============================ pure: tier / multiplier ============================
@given(score=scores)
def test_multiplier_always_in_known_band(score):
    m = tier.multiplier_for(score)
    assert m in _MULTIPLIERS
    assert Decimal("1.00") <= m <= Decimal("1.35")


@given(a=scores, b=scores)
def test_multiplier_is_monotonic_in_score(a, b):
    lo, hi = sorted((a, b))
    assert tier.multiplier_for(lo) <= tier.multiplier_for(hi)


@given(score=scores)
def test_tier_name_and_multiplier_agree(score):
    name = tier.tier_for(score)
    assert name in _TIERS
    # GOAT is the top tier iff the multiplier is the maximum
    assert (name == "GOAT") == (tier.multiplier_for(score) == Decimal("1.35"))
    # Anon is the floor iff the multiplier is 1.00
    assert (name == "Anon") == (tier.multiplier_for(score) == Decimal("1.00"))


# ============================ pure: karma calculation ============================
bases = st.decimals(
    min_value=Decimal("0"), max_value=Decimal("10000"), places=4,
    allow_nan=False, allow_infinity=False,
)


@given(base=bases, score=scores)
def test_karma_is_4dp_bounded_and_never_below_base(base, score):
    karma, mult = tier.karma_for(base, score)
    assert mult in _MULTIPLIERS
    assert karma.as_tuple().exponent == -4              # exactly 4 decimal places
    assert karma >= base                                 # multiplier >= 1.0 → never deflates
    # never inflates past the top tier (allow one quantize step of slack)
    assert karma <= base * Decimal("1.35") + Decimal("0.0001")


@given(base=bases, score=st.floats(min_value=0, max_value=99.9, allow_nan=False))
def test_anon_score_leaves_base_unchanged(base, score):
    karma, mult = tier.karma_for(base, score)
    assert mult == Decimal("1.00")
    assert karma == base.quantize(Decimal("0.0001"))


# ============================ pure: X username extraction ============================
@given(s=st.text(max_size=300))
def test_extract_never_raises_and_output_is_always_valid(s):
    out = extract_x_username(s)        # must never raise on arbitrary text
    if out is not None:
        assert _USERNAME_RE.match(out)  # any non-None result is a syntactically valid handle


@given(name=st.from_regex(r"[A-Za-z0-9_]{1,15}", fullmatch=True))
def test_bare_valid_username_round_trips(name):
    assert extract_x_username(name) == name


# ============================ pure: Telegram HMAC auth ============================
_BOT = "prop-bot:TOKEN-xyz"


def _sign(user: dict, *, auth_date: int | None = None) -> str:
    from urllib.parse import urlencode
    auth_date = auth_date if auth_date is not None else int(time.time())
    pairs = {"auth_date": str(auth_date), "user": json.dumps(user)}
    dcs = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", _BOT.encode(), hashlib.sha256).digest()
    pairs["hash"] = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    return urlencode(pairs)


@given(uid=st.integers(min_value=1, max_value=2**63 - 1), uname=st.text(max_size=20))
def test_correctly_signed_init_data_always_verifies(uid, uname):
    init = _sign({"id": uid, "username": uname})
    user = verify_init_data(init, _BOT)
    assert user["id"] == uid


@given(uid=st.integers(min_value=1, max_value=2**63 - 1))
def test_any_wrong_token_is_rejected(uid):
    init = _sign({"id": uid})
    with pytest.raises(ValueError):
        verify_init_data(init, _BOT + "-tampered")


# ============================ DB: the credit ledger never corrupts ============================
@pytest.fixture(scope="module")
def ledger_schema():
    """Build the schema once for the ledger property (which runs many examples),
    seed a high daily cap, then drop it. Self-managed because Hypothesis invokes
    the test body many times and can't reset an async per-test fixture per example."""
    async def _setup():
        engine = create_async_engine(TEST_DATABASE_URL)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(engine, expire_on_commit=False)
        async with Session() as s:
            s.add(SiteSetting(key="DAILY_EARN_CAP", value="1000000", data_type="int"))
            await s.commit()
        await engine.dispose()

    async def _teardown():
        engine = create_async_engine(TEST_DATABASE_URL)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()

    asyncio.run(_setup())
    site_settings._cache.clear()
    yield
    asyncio.run(_teardown())
    site_settings._cache.clear()


# refund is intentionally excluded: a real refund only ever returns credit from a
# prior spend (cancelled post), so a standalone refund is not a reachable state.
_ops = st.lists(
    st.tuples(
        st.sampled_from(["earn", "spend", "penalty"]),
        st.decimals(min_value=Decimal("0.0001"), max_value=Decimal("5000"), places=4,
                    allow_nan=False, allow_infinity=False),
    ),
    max_size=20,
)


async def _run_ledger(start: Decimal, ops):
    engine = create_async_engine(TEST_DATABASE_URL)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with Session() as s:
            site_settings._cache.clear()
            uid = uuid.uuid4()
            s.add(User(
                id=uid,
                telegram_id=uuid.uuid4().int >> 70,       # unique 58-bit id, fits BigInteger
                referral_code=uuid.uuid4().hex[:10].upper(),
                credits=start, total_credits_earned=start, total_credits_spent=Decimal("0"),
            ))
            await s.commit()

            # CreditService only reads user.id — pass a stand-in so a rollback
            # never triggers a lazy-load on an expired ORM object.
            svc = CreditService(s, SimpleNamespace(id=uid))
            for i, (kind, amt) in enumerate(ops):
                key = f"prop-{i}"
                try:
                    if kind == "earn":
                        await svc.earn(amt, idempotency_key=key)
                    elif kind == "spend":
                        await svc.spend(amt, idempotency_key=key)
                    else:
                        await svc.apply_penalty(amt, admin_id=uid, idempotency_key=key)
                except (InsufficientCreditsError, DailyCapReachedError, ValueError):
                    await s.rollback()  # an expected business rejection — discard & continue

            u = (await s.execute(select(User).where(User.id == uid))).scalar_one()
            # invariants that must hold for ANY sequence (any other outcome — e.g. an
            # IntegrityError from a corrupting write — propagates and fails the test)
            assert u.credits >= Decimal("0")
            assert u.total_credits_earned >= u.total_credits_spent
            assert u.total_credits_earned >= Decimal("0")
            assert u.total_credits_spent >= Decimal("0")
    finally:
        await engine.dispose()


@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    start=st.decimals(min_value=Decimal("0"), max_value=Decimal("10000"), places=4,
                      allow_nan=False, allow_infinity=False),
    ops=_ops,
)
def test_ledger_never_corrupts_for_any_sequence(ledger_schema, start, ops):
    asyncio.run(_run_ledger(start, ops))
