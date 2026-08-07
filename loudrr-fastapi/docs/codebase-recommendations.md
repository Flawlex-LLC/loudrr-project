# Loudrr Codebase Recommendations

This document captures recommended fixes and cleanup work after a first-pass
review of the Loudrr FastAPI/Next.js monorepo.

The overall state is strong: the backend has clear layers, meaningful database
constraints, concurrency-aware credit logic, migrations, background workers,
and a broad test suite. The highest-value work now is to reduce future
maintenance risk, tighten production safety, and make the frontend structure
match the backend's maturity.

## Priority 0: Production Safety

### 1. Add a production configuration guard

**Why:** The app has several safe-in-dev but dangerous-in-prod defaults:
`DEBUG=True` enables the Telegram `?telegram_id=` auth bypass, `SECRET_KEY`
has a development default, `ADMIN_PASSWORD` may be blank, and external service
keys can be unset.

**Recommendation:** Add a startup validation function that fails fast when
`debug=False` and required production values are missing or obviously unsafe.

Suggested checks:

- `DEBUG` must be false in production.
- `SECRET_KEY` must not equal the development default.
- `ADMIN_PASSWORD` must be set if SQLAdmin is mounted.
- `TELEGRAM_BOT_TOKEN` must be set.
- `DATABASE_URL` must not point at a test or local database in production.
- `CORS_ALLOWED_ORIGINS` should be explicit in production.
- `ENCRYPTION_KEY` should be present if redirect URL encryption is used.
- `SENTRY_DSN` or `GLITCHTIP_DSN` should be present for production monitoring.

Suggested location: `backend/app/core/config.py` or a small
`backend/app/core/startup_checks.py` called from `backend/app/main.py`.

### 2. Make the debug Telegram auth bypass harder to misuse

**Why:** The bypass is correctly gated behind `settings.debug`, but this is an
important enough security boundary that it deserves a second guard.

**Recommendation:** Require both `DEBUG=True` and a dedicated setting such as
`ALLOW_DEBUG_TELEGRAM_AUTH=True`.

This makes accidental production exposure less likely if `DEBUG` is mis-set or
if staging uses debug-style logging.

### 3. Replace startup `print()` calls with structured logging

**Why:** The app already configures structured logging, but `main.py` still uses
`print()` for startup status and warnings.

**Recommendation:** Use a module logger in `backend/app/main.py` so startup
events are searchable and consistent with request logs.

Files to update:

- `backend/app/main.py`
- Any scripts that may be used in production automation can keep `print()`;
  runtime app code should prefer logging.

## Priority 1: Transaction And Domain Safety

### 4. Standardize transaction ownership in services

**Why:** Some service methods commit internally, while others support
caller-owned transactions through flags like `commit=False`. That is workable
today, but it becomes surprising as more workflows compose multiple services.

Examples:

- `CreditService.earn(..., commit=True)` can be caller-owned.
- `CreditService.spend()` always commits.
- `CreditService.refund()` always commits.
- `posts.submit_post()` creates a post, calls `spend()`, then commits again.
- `posts.cancel_post()` may call `refund()`, which commits, then mutates the
  post and commits again.

**Recommendation:** Choose one convention and document it.

Preferred direction:

- Service methods should not commit by default.
- Endpoint/task orchestration owns the transaction boundary.
- Services may `flush()` when they need IDs or constraint checks.
- Provide explicit `commit=True` wrappers only for simple one-shot commands if
  needed for backward compatibility.

This is especially important for credit, post escrow, settlement, claims, and
admin adjustments.

### 5. Audit multi-step workflows for partial commit risk

**Why:** Internal commits can split one business operation into multiple
database transactions. If step one commits and step two fails, the domain can
be left in a valid but unintended intermediate state.

Review these flows carefully:

- Post submission and escrow spend.
- Post cancellation and refund.
- Claim batch processing.
- Settlement payouts.
- Admin grant/revoke operations.
- Waitlist approval and Telegram outbox creation.

**Recommendation:** For each workflow, define the intended atomic unit and add
tests that simulate exceptions between sub-steps.

### 6. Keep money-like values as `Decimal` until the response boundary

**Why:** The backend correctly stores credit values as `Decimal`, but some
responses convert values to `float`. That is acceptable for display, but risky
if frontend values are later reused for calculations.

**Recommendation:** Return credit/karma values as strings or normalized integer
minor units if precision matters. If floats are only for UI display, document
that clearly in schemas.

Files to inspect:

- `backend/app/services/posts.py`
- `backend/app/services/users.py`
- `backend/app/services/admin.py`
- `backend/app/schemas/*`

### 7. Add a small domain invariants document

**Why:** The database constraints are good, but future changes need a human
readable contract for the business rules.

Document invariants such as:

- User credits never go below zero.
- Ledger transaction amount is never zero.
- Active post escrow is between zero and initial escrow.
- Completed and cancelled posts have zero escrow.
- Credit idempotency is scoped by `(user_id, type, idempotency_key)`.
- Sponsored XP is separate from spendable credits.
- Admin roles are limited to `""`, `"admin"`, and `"superadmin"`.

Suggested location: `docs/domain-invariants.md`.

## Priority 2: Backend Maintainability

### 8. Reduce long explanatory comments once behavior is tested

**Why:** Some comments are valuable, especially around row locks and async test
isolation. Others read like historical implementation notes. Too many long
comments can make the important ones easier to miss.

**Recommendation:** Keep comments that explain non-obvious behavior and move
broader explanations into docs.

Good comments to keep:

- Lock-order explanations.
- Why `populate_existing=True` matters.
- Why test isolation uses `NullPool`.
- Why readiness checks differ from liveness checks.

Comments to trim:

- Step-by-step comments that restate obvious code.
- Chapter references once the migration from the Django/reference app is done.
- Repeated comments duplicated across methods.

### 9. Split large service modules when they cross domain boundaries

**Why:** Service modules are currently readable, but several domains are growing:
credits, settlement, claims, posts, waitlist, verification, admin.

**Recommendation:** Split only when the module starts mixing unrelated reasons
to change. Good future splits:

- Credit ledger commands vs credit read/reporting helpers.
- Post submission vs post lifecycle transitions.
- Claim queue orchestration vs verification execution.
- Admin read models vs admin mutations.

Avoid premature abstraction; the current service layer is a good baseline.

### 10. Strengthen repository typing

**Why:** `BaseRepository` provides useful shared row operations, but the generic
typing is loose.

**Recommendation:** Consider a SQLAlchemy declarative base protocol/type bound
so repository methods have better model typing. This is lower priority than
domain and production work.

### 11. Add test markers for integration-heavy tests

**Why:** The test suite appears broad and includes real Postgres concurrency.
That is excellent, but developers benefit from a fast local subset.

**Recommendation:** Add markers such as:

- `unit`
- `db`
- `integration`
- `concurrency`
- `external`

Then document commands like:

```powershell
pytest -m "not integration"
pytest -m concurrency
```

Suggested files:

- `backend/pytest.ini`
- `backend/tests/*`
- `backend/README.md`

### 12. Add a migration checklist

**Why:** The test setup manually tracks Postgres enum types in
`backend/tests/conftest.py`. That is fine, but easy to forget.

**Recommendation:** Add a short migration checklist:

- Add/update ORM model.
- Add Alembic migration.
- Add/adjust tests.
- If adding a native Postgres enum, update `_PG_ENUM_TYPES`.
- Verify downgrade if downgrades are supported.
- Seed settings if the feature depends on `SiteSetting`.

Suggested location: `backend/README.md` or `docs/backend-development.md`.

## Priority 3: Frontend Structure

### 13. Split `frontend/lib/api.ts`

**Why:** The API client currently contains mini-app requests, Loud API requests,
admin API requests, domain types, error handling, Telegram auth behavior, and
URL normalization. It is becoming a client-side god file.

**Recommendation:** Split into focused modules:

- `frontend/lib/api/client.ts` for shared request helpers.
- `frontend/lib/api/miniapp.ts` for mini-app endpoints.
- `frontend/lib/api/loud.ts` for Loud endpoints.
- `frontend/lib/api/admin.ts` for admin endpoints.
- `frontend/lib/api/types.ts` or domain-specific type files.
- `frontend/lib/x-url.ts` for X/Twitter URL normalization.

Keep the public exports ergonomic so screens can still import from one barrel
if desired.

### 14. Generate or share API types from backend schemas

**Why:** Frontend interfaces are hand-written and can drift from FastAPI
response models.

**Recommendation:** Use OpenAPI-generated TypeScript types or a lightweight
schema sync process.

Options:

- Generate TS types from FastAPI OpenAPI during development.
- Add contract tests for important endpoints.
- Keep hand-written types but add a checklist requiring frontend type updates
  when backend schemas change.

### 15. Replace the stock frontend README

**Why:** `frontend/README.md` is still the default Next.js README. It does not
explain how this app actually works.

**Recommendation:** Replace it with project-specific notes:

- Required environment variables.
- Local dev commands.
- Telegram WebApp behavior.
- Design mode/mock data behavior.
- API proxy/rewrite behavior.
- Admin dashboard route.

### 16. Document Telegram runtime assumptions

**Why:** A lot of frontend behavior depends on whether the app runs inside
Telegram, localhost, or another host.

**Recommendation:** Document:

- How `initData` is read.
- When `NEXT_PUBLIC_DEBUG_TELEGRAM_ID` is used.
- Which hosts count as development.
- What happens outside Telegram.
- How to test real signed Telegram auth.

Suggested location: `docs/telegram-auth-and-runtime.md`.

### 17. Consider React Query or a similar data layer

**Why:** If more screens fetch and mutate related server state, manual
`useEffect` plus local state may become brittle.

**Recommendation:** If the app grows, introduce a small query/mutation layer
for caching, retries, loading states, and invalidation. Do this when repeated
fetch patterns appear; do not add it only for architecture aesthetics.

## Priority 4: Documentation

### 18. Fix README encoding artifacts

**Why:** The root README displays mojibake sequences for punctuation, which
makes the repo look rough and can confuse copy/pasted instructions.

**Recommendation:** Re-save README files as UTF-8 and replace mojibake with
plain ASCII punctuation or proper Unicode.

Files:

- `README.md`
- Comments in some Python/PowerShell files also show similar artifacts in
  terminal output.

### 19. Fill in `backend/README.md`

**Why:** Backend-specific setup is currently mostly in the root README and
comments. A backend README would help future work.

Recommended sections:

- Environment variables.
- Database setup.
- Alembic migration commands.
- Test database setup.
- Test commands.
- Worker/Redis behavior.
- Site settings seed commands.
- Admin seed commands.
- Common troubleshooting.

### 20. Document deployment topology

**Why:** The code supports web process, worker process, Postgres, Redis, Next.js
standalone output, and proxy rewrites. That topology should be explicit.

Recommended diagram/text:

- Browser/Telegram WebApp -> Next.js.
- Next.js `/api/miniapp/*` and `/api/loud/*` -> FastAPI root routes.
- Next.js `/api/admin/*` -> FastAPI `/api/admin/*`.
- FastAPI -> Postgres.
- FastAPI/worker -> Redis/arq.
- Worker -> outbox, verification batches, cleanup jobs.
- Error tracking -> Sentry or GlitchTip.

### 21. Add an environment variable reference

**Why:** Settings are centralized in `backend/app/core/config.py`, but there is
no clear `.env.example` or env reference in the files reviewed.

**Recommendation:** Add:

- `backend/.env.example`
- `frontend/.env.example`
- `docs/environment.md`

Mark each setting as required, optional, development-only, or production-only.

## Priority 5: Observability And Operations

### 22. Add request/worker correlation to background jobs

**Why:** Request context logging exists for API requests. Background jobs need
equally useful context for claim batches, outbox events, and scheduled tasks.

**Recommendation:** Ensure every worker job logs stable identifiers:

- `job_id`
- `batch_id`
- `user_id`
- `post_id`
- `outbox_event_id`
- retry attempt/count

### 23. Add operational runbooks

**Why:** Once money-like credit state and outbox delivery exist, production
support needs clear recovery steps.

Recommended runbooks:

- Failed outbox messages.
- Stuck verification batch.
- Redis unavailable.
- Postgres unavailable.
- Re-running idempotent jobs.
- Manually correcting a user's credits.
- Revoking compromised admin access.

### 24. Add metrics for core business queues

**Why:** Readiness tells whether dependencies are reachable, but not whether
the product is healthy.

Useful metrics:

- Active posts count.
- Escrow currently locked.
- Pending engagements.
- Pending verification batches.
- Failed verification batches.
- Outbox pending/retry/failed counts.
- Waitlist pending count.
- Admin actions per day.

These can start as admin dashboard stats and later become Prometheus or another
metrics backend.

## Priority 6: Security

### 25. Add a security checklist for admin operations

**Why:** Admin routes can grant/revoke credits, ban users, approve waitlist
entries, and approve X verification. These are sensitive product operations.

**Recommendation:** Document and enforce:

- Which actions require `admin`.
- Which actions require `superadmin`.
- Which actions write audit logs.
- Which actions require a reason.
- Which actions should be rate-limited.

### 26. Review CORS and proxy behavior before launch

**Why:** The frontend uses Next.js rewrites, but the backend also allows direct
CORS origins. Production should be intentionally narrow.

**Recommendation:** Before launch:

- Set `CORS_ALLOWED_ORIGINS` explicitly.
- Confirm cross-origin redirects do not break Telegram WebView behavior.
- Verify admin APIs are not accessible from unexpected origins.

### 27. Protect admin dashboard access at the frontend layer too

**Why:** Backend RBAC is the real control, but the frontend should avoid
showing admin screens to regular users and should handle `403` cleanly.

**Recommendation:** Add a clear frontend admin gate based on `/user/` role or a
dedicated `/api/admin/me/` endpoint.

## Priority 7: Developer Experience

### 28. Add a single command quality check

**Why:** Developers should have one obvious command before committing.

Recommendation:

- Backend: format, lint/type check if available, tests.
- Frontend: lint, typecheck, build.

Possible scripts:

- `scripts/check.ps1`
- `backend/scripts/check.ps1`
- `frontend` package script: `"typecheck": "tsc --noEmit"`

### 29. Add CI once deployment work starts

**Why:** The project already has enough moving parts that CI will quickly pay
for itself.

Recommended CI jobs:

- Backend tests with Postgres service.
- Frontend lint.
- Frontend typecheck.
- Frontend build.
- Alembic migration check.

### 30. Keep local dev scripts, but separate interactive and CI behavior

**Why:** `scripts/dev.ps1` is useful for humans because it opens terminals and
starts services. CI and automation need non-interactive commands.

Recommendation:

- Keep `scripts/dev.ps1`.
- Add non-interactive scripts for check/test/build.
- Avoid prompts in automation scripts.

## Suggested Fix Order

1. Add production startup checks.
2. Add the second guard for debug Telegram auth.
3. Standardize transaction ownership or at least document the current contract.
4. Audit partial commit risk in post/credit/settlement/claim flows.
5. Split `frontend/lib/api.ts`.
6. Replace frontend README and fill backend README.
7. Add `.env.example` files.
8. Add test markers and documented test commands.
9. Replace runtime `print()` calls with structured logging.
10. Add operational runbooks for outbox, claims, and credits.

## Things That Are Already Good

These should be preserved while making changes:

- Thin FastAPI routers.
- Service-layer business logic.
- Database-level constraints for important invariants.
- Decimal usage for credit balances.
- Idempotency keys for credit operations.
- Explicit row locks for concurrent credit mutation.
- Real concurrency tests.
- Alembic as the migration source of truth.
- Separate readiness and liveness probes.
- Centralized frontend API access.
- Next.js proxy rewrites that keep browser calls same-origin in production.
