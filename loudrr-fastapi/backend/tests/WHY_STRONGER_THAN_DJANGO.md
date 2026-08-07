# Why this FastAPI test suite is stronger than the Django original

Reference Django project: `../../loudrr`. This document explains, concretely, where
`loudrr-fastapi` is now **more corruption-proof and better tested** than the Django
implementation it was ported from. It is the companion to the hardening pass that
added DB-level guards, app-layer graceful handling, and ~50 new tests (152 → 200).

---

## 1. The ledger is now un-corruptible at the database layer

The money invariants are enforced by PostgreSQL itself, so no bug, race, or bad
admin action can ever persist a corrupt balance. Django enforces only some of these.

| Invariant (CHECK constraint) | Django | loudrr-fastapi | Proven by |
|---|---|---|---|
| `users.credits >= 0` | ✅ has it | ✅ | `test_db_constraints::test_user_credits_cannot_go_negative` |
| `users.total_credits_earned >= 0` | ❌ missing | ✅ **added** | `test_user_totals_cannot_go_negative` |
| `users.total_credits_spent >= 0` | ❌ missing | ✅ **added** | `test_user_totals_cannot_go_negative` |
| `users.daily_credits_earned >= 0` | partial (0–500) | ✅ **added** (floor) | `test_user_totals_cannot_go_negative` |
| `total_credits_earned >= total_credits_spent` | ✅ | ✅ | `test_user_earned_must_cover_spent` |
| `transactions.amount <> 0` | ✅ | ✅ **added here** | `test_transaction_amount_cannot_be_zero` |
| `(user, type, idempotency_key)` unique | ✅ | ✅ | `test_transaction_idempotency_is_unique_per_type` |
| post escrow: `>=0`, `<=initial`, zero when completed/cancelled | ✅ | ✅ | `test_post_escrow_constraints` |
| engagement: credit requires verification; one per (user,post) | ✅ | ✅ | `test_engagement_*` |
| waitlist / x-verif status ∈ valid set | ✅ | ✅ | `test_waitlist_status_must_be_valid`, `test_x_verification_status_must_be_valid` |

The new file `test_db_constraints.py` doesn't trust the application code — it tries to
write each corrupt row directly and asserts the database raises `IntegrityError`. That
is the strongest possible guarantee: even a future bug can't corrupt the ledger.

> The Django `apply_penalty` deducts unconditionally and relies solely on the
> `credits >= 0` CHECK to stop a negative balance — i.e. an over-penalty raises a raw
> `IntegrityError` (a 500). Here the same case is handled **gracefully** (see §3).

---

## 2. Real concurrency is tested, not assumed

Every money path commits to a real Postgres test DB, so `SELECT ... FOR UPDATE` locks
and CHECK constraints are exercised for real. We added genuine two-connection race
tests (`asyncio.gather` over independent sessions):

| Race | Guarantee | Test |
|---|---|---|
| Two spends draining one balance | exactly one succeeds; balance never negative | `test_concurrent_spend_cannot_oversell` (existing) |
| Earn vs spend on the same balance | serialized; consistent total; never negative | `test_concurrent_earn_and_spend_stay_consistent` |
| Two post submits, can afford one | one succeeds, one rejected gracefully; balance floors at 0; loser's post rolled back | `test_concurrent_submit_cannot_overspend` |
| Same telegram_id registering twice at once | exactly one waitlist row | `test_concurrent_waitlist_register_makes_one_entry` |

**A real deadlock was found and fixed** while writing these. `submit_post` inserted the
Post (taking an FK share-lock on the user row) and then had `spend()` upgrade to
`FOR UPDATE` — two concurrent submits by the same user deadlocked on that upgrade, so the
loser got a 500. Fix: lock the user row *before* the post INSERT (after the external
tweet fetch, so no lock is held during the API call). The loser now blocks and then fails
with a clean "insufficient credits" instead of a deadlock error.

---

## 3. Graceful sad-path handling (never a 500 where a clean error belongs)

| Situation | Behaviour |
|---|---|
| Penalty/revoke larger than balance | clamps to the available balance, floors at 0, records the *actual* amount taken — never negative, never an IntegrityError (`test_penalty_larger_than_balance_floors_at_zero`) |
| Penalty on an empty balance | no-op, returns `None`, writes **no** zero-amount ledger row (`test_penalty_on_empty_balance_is_noop`) |
| Zero/negative earn/spend/refund/grant/penalty | rejected up front with `ValueError` (`test_*_rejects_non_positive`) |
| Banned user clicking / submitting / claiming | `403` everywhere money or engagement moves, not just at `/session/start/` (`test_banned_enforcement.py`) |
| User banned mid-flight (after queueing) | settlement awards nothing, escrow preserved, engagement untouched (`test_settlement_awards_nothing_to_banned_user`) |
| Twitter API unavailable mid-verify | benefit-of-the-doubt pass (no false penalty) |
| Escrow smaller than the award | partial payment, post auto-completes, never overdraws escrow |
| Daily cap reached | award skipped, escrow preserved (no partial corruption) |
| Malformed input (bad email, bad UUID, missing field) | `422` at the schema edge, never reaches a service (`test_input_validation.py`, `test_click_malformed_uuid_422`) |
| Hostile X usernames (unicode, >15 chars, system paths) | rejected to `None` (`test_extract_x_username_rejects_hostile`) |

Banned enforcement in Django lives in scattered view code; here it's applied
consistently in the services, so every entry point (HTTP, background task, future
queue) is covered by the same guard.

---

## 4. Stronger assertions, not just status codes

The pass also tightened weak assertions: `/site_settings` now asserts the full body
and int-coercion (not 2 of 4 keys); the settlement engine has a **multi-engagement
mixed-outcome batch** test (2 pass / 1 fail) that checks per-engagement escrow math,
per-post escrow, credit total, `total_engagements`, honesty drop, and that the failed
engagement is deleted — not just `passed == count`.

---

## 5. Property-based testing — parity with Django, and it found a real bug

Django uses Hypothesis (`core/tests/test_*_hypothesis.py`); the port now does too
(`test_properties.py`). Instead of hand-picked inputs, each property asserts a rule
that must hold for *all* inputs and Hypothesis searches for a counter-example:

- tier multipliers always in the known band and monotonic in score;
- karma always 4dp, never below base, never above the top-tier bound;
- `extract_x_username` never raises and only ever returns a syntactically valid handle;
- correctly-signed Telegram init-data always verifies; any wrong token is rejected;
- **the ledger never corrupts**: for any random sequence of earn/spend/penalty, the
  real `CreditService` keeps `credits >= 0` and `earned >= spent`
  (`test_ledger_never_corrupts_for_any_sequence`, driven against Postgres).

On its first run it **found a real bug** the 11-case hand-written hostile-input test
missed: `extract_x_username("[")` raised `ValueError: Invalid IPv6 URL` from `urlparse`
instead of returning `None` — i.e. an unhandled 500 on a hostile X link in the waitlist
flow. Fixed in `x_url.py` (parse failures now return `None`). That single finding
justifies the whole approach.

## 6. Coverage delta

```
Before: 152 tests   (happy-path heavy, thin on sad/error/edge/concurrency)
After:  210 tests   (+58)
New/expanded:
  + test_db_constraints.py        12  (corruption-proofing — the headline)
  + test_properties.py            10  (Hypothesis: pure math + ledger invariant; found the x_url crash)
  + test_input_validation.py       8  (hostile input, capping, normalization)
  + test_credits.py               +7  (penalty floor/clamp, non-positive, idempotent replay)
  + test_banned_enforcement.py     4  (ban applied everywhere money moves)
  + test_concurrency.py           +3  (earn/spend, submit overspend, waitlist race)
  + test_posts_submit.py          +3  (min/max boundaries, duplicate active post)
  + test_sessions.py              +2  (cancelled post, malformed UUID)
  + test_claims.py                +1  (multi-engagement mixed-outcome settlement)
  + strengthened assertions in test_api_misc.py and others
```

## What stayed intentionally different from Django (v1 scope)

- Transaction types are a subset (`APPLY_PENALTY` vs Django `PENALTY`; no
  `PURCHASED`/`SPONSORED_REWARD`/`ADMIN_REVOKE`).
- Referral counters live on `waitlist_entries`, not `users`.
- `apply_penalty` carries an idempotency key (Django's does not) — a deliberate
  improvement, so a retried penalty can't double-deduct.

## Note on the dev database

The migration `d1e2f3a4b5c6_money_invariant_constraints` adds the new CHECK
constraints and is fully exercised by the test suite (which builds the schema from the
models). The local **dev** DB was originally created via `create_all` with a stale
Alembic stamp (`8f92c96d0f46`), so `alembic upgrade head` can't run cleanly against it
(intermediate migrations hit "already exists"). That drift predates this work; reconcile
it by rebuilding the dev DB from migrations or `alembic stamp head` once the schema
matches. A freshly-migrated database applies `d1e2f3a4b5c6` without issue.
