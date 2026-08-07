# Scaling notes — what it takes to handle ~15k concurrent users

This is the honest capacity picture for the FastAPI backend. **Tests prove
correctness, not scale** — the numbers below come from how the stack behaves
under load, and the only way to *confirm* 15k is a load test (see the bottom).

## What "15k concurrent users" actually means here

15k concurrent users ≠ 15k writes/sec. The traffic mix matters:
- **Reads dominate** — `GET /session/start` (the feed) is most of the traffic and is
  read-scalable (cacheable, read replicas).
- **Writes are a fraction** — click / claim / post-submit. These are the paths with
  row locks and the ones that actually contend.

## Bottlenecks, ranked by what breaks first

### 1. Postgres connections (the #1 risk)
Every web request and every worker job checks out a DB connection. Postgres
defaults to ~100 `max_connections`. The per-process pool is now configurable
([config.py](../app/core/config.py)) and the engine uses `pool_pre_ping`
([db/session.py](../app/db/session.py)):

```
db_pool_size=10, db_max_overflow=20   →  up to 30 connections per process
```

The hard rule:
```
(web_processes + worker_processes) × (db_pool_size + db_max_overflow)  <  Postgres max_connections
```
At 15k concurrent you'll run many uvicorn workers across several instances, which
blows past 100 connections fast. **Put PgBouncer in front in transaction-pooling
mode** — then each app process can keep a small real pool while PgBouncer
multiplexes thousands of client-side connections onto a few dozen server ones.
With PgBouncer, raise nothing; without it, you must keep the product above small.

`pool_pre_ping=True` is on so a dropped/idle/recycled connection (PgBouncer, a
Postgres restart) is detected and replaced transparently instead of erroring a
request.

### 2. Worker throughput vs the Twitter API
Verification batches call twitterapi.io — slow I/O with *their* rate limits. Your
ceiling is roughly `worker_processes × job_concurrency ÷ batch_duration`, capped by
the external rate limit — **not** by arq or Redis. Scale by running more arq worker
processes (`arq app.tasks.worker.WorkerSettings`) and tuning `max_jobs`, but you
can't exceed what twitterapi.io allows. Batch verification is the right design
(it amortizes the API cost); keep `VERIFICATION_BATCH_SIZE` sane.

### 3. Row-lock contention on hot rows
- **Credit ops lock per-USER rows** → different users never contend. Scales well.
- **Settlement locks the POST row** → many users claiming engagements on one viral
  post serialize on that row. The phase-2 critical section is short (no external
  calls), so it's usually tolerable, but a single mega-viral post is the worst case.
- **Post submit** now locks the user row before the INSERT (deadlock fix), so two
  submits by the same user serialize cleanly — a non-issue across different users.

### 4. arq / Redis — rarely the bottleneck
Redis does 100k+ ops/sec; arq enqueues thousands/sec. The one real code issue was
`enqueue()` opening and closing a brand-new Redis pool on *every* call — fixed: it
now reuses one shared pool per process ([enqueue.py](../app/tasks/enqueue.py)),
closed on app shutdown.

## What's tested vs what still needs doing

| | Status |
|---|---|
| Job bodies (settle, outbox drain, expire, reset, tweetscout) | ✅ unit-tested directly |
| **Real enqueue → Redis → worker round-trip** | ✅ `test_arq_integration.py` (runs against a live Redis, skips fast if none) |
| Shared arq pool (no per-call churn) | ✅ implemented + exercised by the integration test |
| DB pool sizing + pre-ping | ✅ configurable + on |
| **Load test (throughput/latency at 15k)** | ❌ NOT done — this is the only thing that *confirms* the target |

## To actually confirm 15k: load test
Port the Django project's `locustfile.py` (it already exists at
`../../loudrr/backend/locustfile.py`) or write a Locust scenario hitting the hot
paths (feed-heavy read mix + a slice of click/claim/submit), and run it against a
staging deploy with PgBouncer + N workers. Watch: p95 latency, Postgres active
connections, lock waits (`pg_stat_activity` wait events), and arq queue depth.
That tells you the real ceiling and where to add capacity.
