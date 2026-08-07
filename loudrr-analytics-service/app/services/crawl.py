"""Crawl job — build/refresh the reverse index (research §6, §11 step 3).

For each smart-set member m, fetch m's *following* list and upsert (m -> X) edges.
That single dataset is the whole product: the reverse index for any X AND the
M-internal graph for PageRank.

Profiles-only: the verified endpoint catalog exposes just ``/user/followings``
(full profiles, max 200/page) — there is no bulk following-IDs endpoint (see
app/clients/twitterapi.py). Each followee profile already carries the ``id``, so an
edge is (m.user_id -> profile.id) with no extra resolution.

Resume model — **DB-authoritative** (not a side file). A member is "done" iff its
``last_crawled_at`` is non-NULL, which is committed *after* its edges are flushed.
``_select_members`` only returns members with NULL ``last_crawled_at``, so:
  * completed members never consume a ``--limit`` slot (no starvation),
  * a member that fails transiently stays NULL and is retried on the next run,
  * a crash mid-member re-crawls exactly that one member next run (edges are
    idempotent via on_conflict_do_nothing, so re-spend is bounded to ~one member
    and never corrupts data). This is the deliberate tradeoff vs. silent edge-loss.
A 404 (account gone) is marked done so it isn't retried forever.

Budget guard — **credit-native and fail-safe**. It stops on the exact wallet-credit
delta from /oapi/my/info (re-synced every member). If the balance becomes unreadable
mid-run it does NOT freeze at a stale value: after ``_MAX_SYNC_FAILS`` consecutive
failed reads it stops the run ("balance_stale"), so a degraded balance endpoint can
never silently disable the guard and drain the wallet. The per-call estimate is only
a fallback when the *initial* balance read fails (conservative — it over-counts on
the gateway, so it stops early rather than over-spending).

The crawl writes ONLY the edges table + SmartSetMember.last_crawled_at — it never
touches harvested_scores or other smart_set columns.
"""
from __future__ import annotations

import asyncio
import logging
import time
from types import SimpleNamespace

import httpx
from sqlalchemy import func, select, update
from sqlalchemy.exc import OperationalError

from app.clients.twitterapi import TwitterAPIClient
from app.core.config import settings
from app.db.models import Edge, SmartSetMember
from app.db.session import SessionLocal

logger = logging.getLogger(__name__)

# Dialect-portable upsert: sqlite/pg both support .on_conflict_do_nothing.
_IS_SQLITE = settings.database_url.startswith("sqlite")
if _IS_SQLITE:
    from sqlalchemy.dialects.sqlite import insert as _dialect_insert
else:  # pragma: no cover - exercised under Postgres
    from sqlalchemy.dialects.postgresql import insert as _dialect_insert

# Edges insert only 2 bound columns/row (seen_at uses a server default), so ≤499
# rows/statement stays under SQLite's 999 bind-var cap. 300 leaves margin.
_EDGE_CHUNK = 300
_MAX_SYNC_FAILS = 3  # consecutive failed balance reads before we stop (fail-safe)
_GONE_CONFIRM = 3    # consecutive 404s before we trust "account gone" (guards spurious 404)
_BALANCE_SYNC_EVERY = 20  # re-sync wallet every N completed members
_PROGRESS_EVERY = 10      # log a progress heartbeat every N completed members
_COMPLETENESS_MIN = 0.90  # require >= this fraction of the profile's following-count, else re-crawl

# HTTP statuses that mean "every member will fail" (auth / out of credits) -> stop the
# whole run cleanly, not skip-and-continue.
_TERMINAL_STATUSES = {401, 402, 403}

# SQLite is single-writer. Under concurrency the workers' commits would collide
# ("database is locked"; aiosqlite doesn't reliably honor busy_timeout in async), so ALL DB
# writes go through this one lock — exactly one writer at a time, no contention, no lost edge.
# Reads (member selection, balance) are network/WAL and stay concurrent.
_db_write_lock = asyncio.Lock()


async def _write_retry(do, *, tries: int = 12):
    """Run an async DB-write thunk, retrying on 'database is locked' (backstop for any
    inter-process writer holding the lock). The in-process _db_write_lock already serializes
    our own workers; this guards against an external process touching the same file."""
    for i in range(tries):
        try:
            return await do()
        except OperationalError as e:
            if "locked" in str(e).lower() and i < tries - 1:
                await asyncio.sleep(0.25 + 0.4 * i)
                continue
            raise


async def _flush_edges(follower_id: str, followee_ids: list[str]) -> int:
    """Upsert (follower_id -> followee_id) edges, deduped, in bind-safe chunks. Serialized +
    retry-guarded so concurrent workers never lose edges to a write-lock. Returns the number
    of unique rows written (incl. conflicts skipped)."""
    if not followee_ids:
        return 0
    unique = list(dict.fromkeys(followee_ids))  # preserve order, drop dups in-batch

    async def _do():
        async with SessionLocal() as session:
            for i in range(0, len(unique), _EDGE_CHUNK):
                rows = [
                    {"follower_id": follower_id, "followee_id": fid}
                    for fid in unique[i : i + _EDGE_CHUNK]
                ]
                stmt = _dialect_insert(Edge).values(rows).on_conflict_do_nothing(
                    index_elements=[Edge.follower_id, Edge.followee_id]
                )
                await session.execute(stmt)
            await session.commit()

    async with _db_write_lock:
        await _write_retry(_do)
    return len(unique)


async def _mark_crawled(user_id: str) -> None:
    """Commit last_crawled_at=now() — the durable 'this member is done' signal. Serialized +
    retry-guarded (same write-lock as edges) so it can't be lost to contention; a member is
    marked done ONLY after its edges are committed, so a lost mark just re-crawls (idempotent)."""
    async def _do():
        async with SessionLocal() as session:
            await session.execute(
                update(SmartSetMember)
                .where(SmartSetMember.user_id == user_id)
                .values(last_crawled_at=func.now())
            )
            await session.commit()

    async with _db_write_lock:
        await _write_retry(_do)


_following_count_cache: dict[str, int | None] = {}


async def _real_following_count(tw: TwitterAPIClient, user_id: str) -> int | None:
    """The member's TRUE following count, for verifying a crawl actually finished.

    This is the ground truth the endpoint's `has_next_page` can't be trusted to give us.
    Cached per-process so the 10 in-run retries don't pay for 10 profile lookups. Returns
    None if the profile can't be read — in that case we do NOT block the crawl (better to
    accept a possibly-short crawl than to stall the whole pass on a flaky profile read).
    """
    if user_id in _following_count_cache:
        return _following_count_cache[user_id]
    try:
        prof = await tw.get_user_info_by_id(user_id)
        n = int((prof or {}).get("following") or 0) or None
    except Exception as e:  # noqa: BLE001 — never let verification break the crawl
        logger.warning("could not read following count for %s (%s) — skipping verification",
                       user_id, type(e).__name__)
        n = None
    _following_count_cache[user_id] = n
    return n


async def _ensure_failures_table() -> None:
    """Create the persistent per-request failure log (gateway diagnostics). Postgres only."""
    if _IS_SQLITE:
        return
    from sqlalchemy import text

    async def _do():
        async with SessionLocal() as session:
            await session.execute(text(
                "CREATE TABLE IF NOT EXISTS crawl_failures ("
                "id BIGSERIAL PRIMARY KEY, ts DOUBLE PRECISION, logged_at TIMESTAMPTZ DEFAULT now(), "
                "endpoint VARCHAR, user_id VARCHAR, status INT, kind VARCHAR, attempt INT, "
                "request_id VARCHAR, error TEXT)"))
            await session.commit()

    async with _db_write_lock:
        await _write_retry(_do)


async def _flush_failures(records: list[dict]) -> None:
    """Persist drained failure records. Best-effort — a logging hiccup must never break the crawl."""
    if _IS_SQLITE or not records:
        return
    from sqlalchemy import text

    async def _do():
        async with SessionLocal() as session:
            await session.execute(text(
                "INSERT INTO crawl_failures (ts, endpoint, user_id, status, kind, attempt, request_id, error)"
                " VALUES (:ts, :endpoint, :user_id, :status, :kind, :attempt, :request_id, :error)"), records)
            await session.commit()

    try:
        async with _db_write_lock:
            await _write_retry(_do)
    except Exception as e:  # noqa: BLE001 - diagnostics must not interfere with the crawl
        logger.warning("failure-log flush failed (%s)", e)


async def _select_members(
    limit: int | None, *, random_order: bool = False
) -> list[tuple[str, str | None]]:
    """Members still to crawl (last_crawled_at IS NULL).

    Filtering on NULL makes resume DB-authoritative: completed members are excluded
    at the SQL level so they never consume a --limit slot, and failed members (still
    NULL) are legitimately retried on the next run.

    ``random_order`` draws a representative random sample (for an unbiased cost
    measurement) instead of the default insertion order.
    """
    async with SessionLocal() as session:
        q = (
            select(SmartSetMember.user_id, SmartSetMember.username)
            .where(SmartSetMember.last_crawled_at.is_(None))
        )
        q = q.order_by(func.random()) if random_order else q.order_by(
            SmartSetMember.added_at.asc(), SmartSetMember.user_id.asc()
        )
        if limit:
            q = q.limit(limit)
        return list((await session.execute(q)).all())


async def _true_credits(tw: TwitterAPIClient) -> int | None:
    """Best-effort wallet balance; None if unreadable (caller decides what to do)."""
    try:
        return await tw.total_credits()
    except Exception as e:  # noqa: BLE001 - balance is advisory at the call site
        logger.warning("balance read failed (%s)", e)
        return None


async def _crawl_member(tw: TwitterAPIClient, st, stop_event: "asyncio.Event",
                        user_id: str, username: str) -> None:
    """Crawl ONE member's COMPLETE following list via the bulk-IDs endpoint, then update ``st``.

    Completeness is VERIFIED against the member's real following count — it is NOT taken on the
    endpoint's word. The gateway intermittently reports ``has_next_page=False`` mid-list (it
    also throws sporadic 503s), and the crawler used to believe it: @JohnCena was stored with
    55k of his 1.06M following and @FerreWeb3 with 78% of his, each silently marked "done".
    Now a member is only marked crawled when we hold >= CRAWL_COMPLETE_AT of their real
    following count; short crawls are retried, then deferred to the next pass.

    (The count itself is a lower bound in practice: X's `following` includes suspended/deleted
    accounts the API won't return, which is why the bar is ~0.9 and not 1.0.)

    Transient errors are retried hard in-run; a confirmed 404 is marked gone; if still failing
    the member is deferred (left NULL) and retried next pass. Terminal auth/quota stops the pool.
    """
    gone_404 = 0
    for attempt in range(1, settings.crawl_member_max_retries + 1):
        if stop_event.is_set():
            return
        try:
            ids: list[str] = []
            async for fid in tw.iter_following_ids(
                user_id, max_items=settings.crawl_max_following_per_member
            ):
                ids.append(fid)

            # VERIFY: did we actually get their whole list? (the bug this guards against is
            # silent truncation, which produces no error at all)
            if settings.crawl_verify_completeness and not settings.crawl_max_following_per_member:
                real = await _real_following_count(tw, user_id)
                if real and len(ids) < real * settings.crawl_complete_at:
                    # keep what we got (idempotent upsert), but do NOT mark done
                    st.total_edges += await _flush_edges(user_id, ids)
                    raise RuntimeError(
                        f"incomplete: {len(ids)} of {real} following "
                        f"({len(ids)/real*100:.0f}%) — refusing to mark crawled")

            st.total_edges += await _flush_edges(user_id, ids)
            await _mark_crawled(user_id)
            st.crawled += 1
            return
        except httpx.HTTPStatusError as e:
            code = e.response.status_code
            if code in _TERMINAL_STATUSES:
                logger.error("terminal %s on @%s -> stopping (auth/quota)", code, username)
                st.stopped = "quota"; stop_event.set(); return
            if code == 404:
                gone_404 += 1
                if gone_404 >= _GONE_CONFIRM:  # confirmed gone/suspended -> done, don't retry forever
                    st.gone += 1
                    await _mark_crawled(user_id)
                    return
                err = f"404 (x{gone_404}, confirming)"
            else:
                err = f"HTTP {code}"
        except Exception as e:  # noqa: BLE001 - transient network/parse/empty-truncation guard
            err = str(e) or type(e).__name__
        st.reattempts += 1
        logger.warning("  @%s attempt %d/%d failed (%s) -> retry", username, attempt,
                       settings.crawl_member_max_retries, err)
        await asyncio.sleep(min(60, 2 ** attempt))

    # exhausted retries — DEFER (left NULL), retried next pass; never silently skipped
    if not stop_event.is_set():
        st.failed += 1
        logger.error("@%s STILL failing after %d attempts -> DEFERRED (retried next pass)",
                     username, settings.crawl_member_max_retries)


async def crawl(
    *,
    limit: int | None = None,
    budget_credits: float | None = None,
    budget_usd: float | None = None,
    random_order: bool = False,
    concurrency: int | None = None,
) -> dict:
    """Crawl following lists for smart-set members CONCURRENTLY. Returns a run summary.

    ``concurrency`` members are crawled in parallel (default settings.crawl_concurrency),
    all sharing the client's single QPS limiter — so the global request rate is still
    capped, but the idle wait between request round-trips is filled and a heavy-follower
    no longer blocks the line. Serial-grade safety is preserved: see module docstring for
    the DB-authoritative resume model and the fail-safe credit guard.
    """
    tw = TwitterAPIClient()  # ONE client -> one shared QPS limiter across all workers
    rate = tw.credits_per_usd
    if budget_credits is not None:
        budget_cr = budget_credits
    elif budget_usd is not None:
        budget_cr = budget_usd * rate
    else:
        budget_cr = settings.crawl_daily_budget_usd * rate
    conc = concurrency or settings.crawl_concurrency

    start_credits = await _true_credits(tw)
    logger.info(
        "crawl start: provider=%s (%s), concurrency=%d, budget %s credits (~$%.2f), wallet %s credits",
        tw.provider, tw.root_url, conc, f"{budget_cr:,.0f}", budget_cr / rate,
        f"{start_credits:,}" if start_credits is not None else "UNREADABLE (estimate-only guard)",
    )

    await _ensure_failures_table()
    members = await _select_members(limit, random_order=random_order)
    queue: asyncio.Queue = asyncio.Queue()
    for m in members:
        queue.put_nowait(m)

    # shared mutable state (safe under asyncio: single thread, mutated between awaits)
    st = SimpleNamespace(crawled=0, total_edges=0, skipped_no_username=0, gone=0, failed=0,
                         reattempts=0, done=0, last_balance=start_credits,
                         sync_fails=0, stopped="queue_empty")
    t0 = time.monotonic()

    def _spent_credits() -> float:
        if start_credits is not None and st.last_balance is not None:
            return start_credits - st.last_balance
        return tw.credits_spent

    async def _post_member() -> None:
        """Periodic balance re-sync (every _BALANCE_SYNC_EVERY) + progress heartbeat (every
        _PROGRESS_EVERY) — cheap under concurrency."""
        st.done += 1
        if st.done % _BALANCE_SYNC_EVERY == 0:
            bal = await _true_credits(tw)
            if bal is not None:
                st.last_balance = bal
                st.sync_fails = 0
            else:
                st.sync_fails += 1
        if st.done % _PROGRESS_EVERY == 0:
            logger.info("  progress: %d crawled, %d gone, %d deferred, %d edges, ~%s cr (~$%.2f), queue %d",
                        st.crawled, st.gone, st.failed, st.total_edges, f"{_spent_credits():,.0f}",
                        _spent_credits() / rate, queue.qsize())
        # persist any per-request failures captured by the client (gateway diagnostics)
        await _flush_failures(tw.drain_failures())

    async def worker() -> None:
        while not stop_event.is_set():
            try:
                user_id, username = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                if _spent_credits() >= budget_cr:
                    logger.warning("budget %s credits hit -> stopping", f"{budget_cr:,.0f}")
                    st.stopped = "budget"
                    stop_event.set()
                    return
                if start_credits is not None and st.sync_fails >= _MAX_SYNC_FAILS:
                    logger.error("balance unreadable %d syncs -> stopping (fail-safe)", st.sync_fails)
                    st.stopped = "balance_stale"
                    stop_event.set()
                    return
                # username is optional — iter_following_ids fetches by user_id; username is only
                # for log labels. Crawl user_id-only members (e.g. expansion promotions) instead
                # of skipping them (the old guard marked them done WITHOUT crawling).
                await _crawl_member(tw, st, stop_event, user_id, username or user_id)
                await _post_member()
            finally:
                queue.task_done()

    stop_event = asyncio.Event()
    await asyncio.gather(*[asyncio.create_task(worker()) for _ in range(conc)])
    await _flush_failures(tw.drain_failures())  # final drain of any tail failures

    end_credits = await _true_credits(tw)
    measured_cr = (
        start_credits - end_credits
        if start_credits is not None and end_credits is not None
        else None
    )
    dur = time.monotonic() - t0
    summary = {
        "provider": tw.provider,
        "concurrency": conc,
        "duration_s": round(dur, 1),
        "members_crawled": st.crawled,                # each paginated cleanly to has_next_page=False
        "edges_written": st.total_edges,
        "accounts_gone_404": st.gone,
        "failed_will_retry": st.failed,
        "skipped_no_username": st.skipped_no_username,
        "stopped": st.stopped,
        # --- gateway performance metrics ---
        "api_calls_ok": tw.calls,
        "api_attempts": tw.attempts,
        "api_retries": tw.retries,
        "api_failures": dict(tw.failures),            # {http_502: n, http_429: n, timeout: n, ...}
        "recrawl_events": st.reattempts,              # completeness-driven re-crawls
        "ids_per_min": round(st.total_edges / dur * 60) if dur > 0 else 0,
        "calls_per_min": round(tw.calls / dur * 60, 1) if dur > 0 else 0,
        "members_per_min": round(st.crawled / dur * 60, 1) if dur > 0 else 0,
        # --- cost ---
        "credits_estimated": round(tw.credits_spent, 1),
        "credits_measured": measured_cr,
        "credits_per_member": (
            round(measured_cr / st.crawled, 1) if measured_cr is not None and st.crawled else None
        ),
        "usd_measured": round(measured_cr / rate, 4) if measured_cr is not None else None,
    }
    logger.info("crawl done: %s", summary)
    return summary
