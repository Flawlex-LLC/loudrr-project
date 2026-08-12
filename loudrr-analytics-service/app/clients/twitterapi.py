"""twitterapi.io client — the sole data source for the crawl.

Auth: ``x-api-key`` header (case-insensitive; lowercase to match the docs). Read
endpoints live under ``https://api.twitterapi.io/twitter``; the account-balance
endpoint is at the root (``/oapi/my/info``).

The reverse index is built from each member's *following* list. The **verified
endpoint catalog** (see .agents/skills/twitterapi-io/references/endpoints.md,
checked against the live backend) exposes exactly one way to get it:

    GET /twitter/user/followings?userName=&cursor=&pageSize=  (pageSize 20–200)
        -> {followings:[{id, userName, ...}], has_next_page, next_cursor}

There is **no** bulk ``followings_ids`` endpoint — the pricing page implied one but
the verified catalog (which lists followers / verifiedFollowers / followings) does
not, and the guessed slug 404'd. So the crawl is profiles-only; each followee
profile already carries the ``id`` we need for an edge, so no extra resolution.

Credit costs below are **estimates** for the pre-flight/in-loop budget guard; the
crawl re-syncs to the true spend via ``get_balance`` (1 USD = 100k credits).
All IDs are kept as strings (snowflake precision).
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator

import httpx
from aiolimiter import AsyncLimiter
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
)

from app.core.config import settings

logger = logging.getLogger(__name__)

# Default upstream. The loudrr gateway (settings.gateway_base_url) is a drop-in,
# twitterapi.io-compatible service and is preferred whenever LOUDRR_GATEWAY_API is set
# (verified 2026-06-18: identical /twitter + /oapi paths, params, response shapes, and
# x-api-key auth). Only the host/key change; nothing downstream cares which it is.
TWITTERAPI_ROOT = "https://api.twitterapi.io"
_TIMEOUT = httpx.Timeout(45.0)

# Stop this many requests short of the gateway's advertised budget, so we pause on our own
# terms instead of making it reject us.
_RL_FLOOR = 200
# HARD ceiling on concurrent gateway requests, whatever a caller asks for. The gateway runs a
# shared pool of ~45 aux accounts; sustained concurrency 32 from us drained it and took the
# whole service down for every client ("no aux account free for users_show: 45 in-flight,
# 0 cooling of 45", 2026-07-14). Their capacity is not ours to saturate — never tune this up
# to find the breaking point again.
MAX_GATEWAY_CONCURRENCY = 6

# Neutral browser UA on every outbound request (consistent with sorsa.py /
# seed_coingecko.py) — never ship an identifiable / identityless client string.
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Per-item credit estimate for the following-PROFILES endpoint, tiered by page size
# (twitterapi.io/pricing): 200/page -> 1 credit ($0.01/1k). Guard-only; the real
# number comes from the balance delta.
_FOLLOWING_PROFILE_CREDITS = {200: 1, 100: 2, 20: 3}  # by >= page size
_MIN_CREDITS_PER_CALL = 60
_EMPTY_PAGE_RETRIES = 5  # re-fetch an empty page that claims has_next_page before trusting it


def _per_item_credits(table: dict[int, float], page_size: int) -> float:
    for threshold in sorted(table, reverse=True):
        if page_size >= threshold:
            return table[threshold]
    return max(table.values())


def _is_transient(exc: BaseException) -> bool:
    """Retry only transient failures: network errors + 429/5xx. A 4xx (404 deleted
    account, 401/402/403 auth/quota) is NOT transient — raise it immediately so the
    crawl can skip the account or stop cleanly instead of burning 4 pointless retries."""
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        return code == 429 or code >= 500
    return False


def _wait_honor_retry_after(retry_state) -> float:
    """tenacity wait: HONOR the gateway's ``Retry-After`` header on 429/503 (per the gateway
    team's new rate-mechanism, 2026-06-21) — wait exactly as long as they ask. Fall back to
    exponential backoff when there's no header."""
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    if isinstance(exc, httpx.HTTPStatusError):
        ra = exc.response.headers.get("retry-after")
        if ra:
            try:
                return min(120.0, float(ra))           # seconds form
            except ValueError:
                pass
    return min(30.0, 2.0 ** retry_state.attempt_number)


class TwitterAPIClient:
    def __init__(self, api_key: str | None = None, qps: int | None = None,
                 rate_calls: int | None = None, rate_window_s: int | None = None):
        # Prefer the loudrr gateway when configured; else fall back to api.twitterapi.io.
        if settings.loudrr_gateway_api:
            self.provider = "loudrr-gateway"
            self.root_url = settings.gateway_base_url.rstrip("/")
            default_key = settings.loudrr_gateway_api
        else:
            self.provider = "twitterapi.io"
            self.root_url = TWITTERAPI_ROOT
            default_key = settings.twitterapi_io_key
        self.base_url = f"{self.root_url}/twitter"
        self.api_key = api_key if api_key is not None else default_key
        # aiolimiter (MIT) leaky-bucket. Gateway team (2026-06-21): ~20-40 req/min. We pace to
        # crawl_rate_calls per crawl_rate_window_s (30/min) AND honor Retry-After (see _get) so
        # their rate-mechanism can throttle us directly. An explicit qps overrides (per-second);
        # rate_calls/rate_window_s gives a caller its OWN window pacing (e.g. the engagement
        # tracker runs 15/min so it coexists with the crawl inside the gateway band).
        if qps:
            self._limiter = AsyncLimiter(qps, 1)
        elif rate_calls:
            self._limiter = AsyncLimiter(rate_calls, rate_window_s or 60)
        else:
            self._limiter = AsyncLimiter(settings.crawl_rate_calls, settings.crawl_rate_window_s)
        self.credits_spent: float = 0.0
        # --- perf counters (for gateway performance reporting) ---
        self.attempts = 0                      # every HTTP attempt incl. retries
        self.calls = 0                         # successful (2xx) responses
        # Gateway's advertised budget (ratelimit-* headers), refreshed on every response.
        self.rl_limit: int | None = None
        self.rl_remaining: int | None = None
        self.rl_reset: int | None = None
        self.failures: dict[str, int] = {}     # by kind: http_429 / http_502 / network / timeout
        # Per-request failure records for gateway diagnostics (drained + persisted by the crawl):
        # each = {ts, endpoint, user_id, status, kind, attempt, request_id, error}.
        self.failure_log: list[dict] = []

    def _note_fail(self, kind: str) -> None:
        self.failures[kind] = self.failures.get(kind, 0) + 1

    def _record_failure(self, path: str, params: dict, *, status, kind: str, resp, err: str = "") -> None:
        """Capture one failed request with enough detail for the gateway team to trace it on
        their side (the response's request_id, the status, which userId, attempt #, timestamp)."""
        rid = ""
        if resp is not None:
            rid = resp.headers.get("x-request-id") or resp.headers.get("cf-ray") or ""
            try:
                body = resp.json()
                rid = rid or str(body.get("request_id") or "")
                if not err:
                    err = str(body.get("msg") or body.get("detail") or "")
            except Exception:  # noqa: BLE001 - body may be non-JSON
                if not err:
                    try:
                        err = (resp.text or "")[:200]
                    except Exception:  # noqa: BLE001
                        err = ""
        self.failure_log.append({
            "ts": time.time(),
            "endpoint": path,
            "user_id": str(params.get("userId") or params.get("userName") or ""),
            "status": status,
            "kind": kind,
            "attempt": self.attempts,
            "request_id": rid,
            "error": (err or "")[:300],
        })

    def drain_failures(self) -> list[dict]:
        """Return and clear the buffered failure records (the crawl persists them to the DB)."""
        out = self.failure_log
        self.failure_log = []
        return out

    @property
    def retries(self) -> int:
        """Attempts beyond the first per logical success — i.e. how often we had to retry."""
        return max(0, self.attempts - self.calls)

    @property
    def credits_per_usd(self) -> float:
        """Wallet credit->USD rate for the active provider (gateway differs from
        twitterapi.io). For human-facing display only; guards are credit-native."""
        if self.provider == "loudrr-gateway":
            return settings.gateway_credits_per_usd
        return float(settings.credits_per_usd)

    @property
    def usd_spent(self) -> float:
        return self.credits_spent / self.credits_per_usd

    def _client(self, base: str | None = None) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=base or self.base_url, timeout=_TIMEOUT,
            headers={"x-api-key": self.api_key, "User-Agent": _UA},
        )

    @retry(
        retry=retry_if_exception(_is_transient),
        wait=_wait_honor_retry_after,
        stop=stop_after_attempt(8),
        reraise=True,
    )
    def _read_ratelimit(self, resp: httpx.Response) -> None:
        """Track the gateway's advertised budget (ratelimit-limit / -remaining / -reset).

        The gateway added these after we exhausted its aux-account pool with sustained
        concurrency (503: "no aux account free for users_show: 45 in-flight, 0 cooling of 45").
        Reading them is how we stay inside ITS ceiling instead of discovering it by breaking it.
        """
        try:
            rem = resp.headers.get("ratelimit-remaining")
            if rem is not None:
                self.rl_remaining = int(rem)
            lim = resp.headers.get("ratelimit-limit")
            if lim is not None:
                self.rl_limit = int(lim)
            rst = resp.headers.get("ratelimit-reset")
            if rst is not None:
                self.rl_reset = int(rst)
        except (TypeError, ValueError):
            pass

    async def _respect_gateway_budget(self) -> None:
        """Back off BEFORE the gateway has to reject us. If the advertised remaining budget is
        nearly spent, sleep until the window resets rather than spraying doomed requests."""
        if self.rl_remaining is None:
            return
        if self.rl_remaining <= _RL_FLOOR:
            wait = max(1, self.rl_reset or 5)
            logger.warning("gateway budget nearly spent (%s left of %s) — pausing %ss for reset",
                           self.rl_remaining, self.rl_limit, wait)
            await asyncio.sleep(wait)
            self.rl_remaining = None      # force a fresh read after the window rolls

    async def _get(self, client: httpx.AsyncClient, path: str, params: dict) -> dict:
        await self._limiter.acquire()
        await self._respect_gateway_budget()
        self.attempts += 1
        try:
            resp = await client.get(path, params=params)
            self._read_ratelimit(resp)
            # 429/5xx -> retried (transient); other 4xx -> raised immediately (see _is_transient).
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            self._read_ratelimit(e.response)
            self._note_fail(f"http_{e.response.status_code}")
            self._record_failure(path, params, status=e.response.status_code,
                                 kind=f"http_{e.response.status_code}", resp=e.response)
            raise
        except httpx.TimeoutException:
            self._note_fail("timeout")
            self._record_failure(path, params, status=None, kind="timeout", resp=None)
            raise
        except httpx.TransportError as e:
            self._note_fail("network")
            self._record_failure(path, params, status=None, kind="network", resp=None, err=str(e))
            raise
        self.calls += 1
        return resp.json()

    # ---- account ---------------------------------------------------------

    async def get_balance(self) -> dict:
        """Live wallet balance: ``{recharge_credits, total_bonus_credits}`` (credits).
        Routed through ``_get`` so transient blips (429/5xx) are retried — the budget
        guard depends on this read staying reliable."""
        async with self._client(self.root_url) as client:
            return await self._get(client, "/oapi/my/info", {})

    async def total_credits(self) -> int | None:
        """Total spendable credits remaining (recharge + bonus). Returns None if the
        response carries neither expected field (treat as unreadable, not as 0 — a
        spurious 0 would silently disable the crawl's budget guard)."""
        bal = await self.get_balance()
        if "recharge_credits" not in bal and "total_bonus_credits" not in bal:
            logger.warning("balance response missing credit fields: %s", list(bal)[:6])
            return None
        return int(bal.get("recharge_credits", 0)) + int(bal.get("total_bonus_credits", 0))

    # ---- profiles --------------------------------------------------------

    async def get_user_info(self, username: str) -> dict | None:
        """Resolve a handle -> profile dict (incl. id, counts). None if missing."""
        async with self._client() as client:
            data = await self._get(client, "/user/info", {"userName": username.lstrip("@")})
        self.credits_spent += 18  # $0.18/1k single-profile tier
        return data.get("data") or (data if data.get("id") else None)

    async def get_user_info_by_id(self, user_id: str) -> dict | None:
        """Profile by NUMERIC id (the gateway's /user/info accepts ?userId=; note it does NOT
        accept a numeric id passed as ?userName= — that returns "user not found").
        Used to verify a crawl's completeness against the member's real `following` count."""
        async with self._client() as client:
            data = await self._get(client, "/user/info", {"userId": str(user_id)})
        self.credits_spent += 18  # $0.18/1k single-profile tier
        return data.get("data") or (data if data.get("id") else None)

    async def batch_info_by_ids(self, user_ids: list[str]) -> list[dict]:
        """Batch profiles by ID (10 credits/user at >=100 ids). Chunk at 100.

        NOTE: the loudrr gateway returns 404 for this endpoint (verified 2026-06-18) —
        it proxies the crawl-critical reads (user/info, user/followings, oapi/my/info)
        but not batch_info_by_ids. It's unused on the crawl/seed/score path; if a future
        path needs it on the gateway, fall back to per-id get_user_info or add it upstream.
        """
        out: list[dict] = []
        async with self._client() as client:
            for i in range(0, len(user_ids), 100):
                chunk = user_ids[i : i + 100]
                data = await self._get(
                    client, "/user/batch_info_by_ids", {"userIds": ",".join(chunk)}
                )
                users = data.get("users") or data.get("data") or []
                out.extend(users)
                self.credits_spent += 10 * len(chunk)
        return out

    # ---- following crawl: profiles (the only verified path) --------------

    async def iter_following_ids(
        self, user_id: str, *, max_items: int | None = None
    ) -> AsyncIterator[str]:
        """Yield followee user-IDs for ``user_id`` via the gateway's bulk ``/user/followings_ids``
        endpoint — **5,000 IDs/page** (vs ~50 for the profiles path), no truncation, ~$0.0049/1k.
        IDs are all the crawl needs for the edge graph. Takes the **numeric userId**.

        Terminates on ``has_next_page is False``; an empty page that claims more is retried then
        raised (never silently truncates), same discipline as the profiles iterator.
        """
        page = settings.crawl_ids_page_size
        cursor, seen, empty_retries = "", 0, 0
        async with self._client() as client:
            while True:
                data = await self._get(
                    client, "/user/followings_ids",
                    {"userId": str(user_id), "pageSize": page, "cursor": cursor},
                )
                ids = data.get("ids") or []
                has_next = bool(data.get("has_next_page"))
                next_cursor = data.get("next_cursor") or ""
                if not ids:
                    self.credits_spent += _MIN_CREDITS_PER_CALL
                    if has_next and empty_retries < _EMPTY_PAGE_RETRIES:
                        empty_retries += 1
                        await asyncio.sleep(min(8, 2 ** empty_retries))
                        continue
                    if has_next:
                        raise RuntimeError(
                            f"userId={user_id}: empty followings_ids page with has_next_page=True "
                            f"after {_EMPTY_PAGE_RETRIES} retries — refusing to truncate")
                    return
                empty_retries = 0
                self.credits_spent += max(int(0.49 * len(ids)), _MIN_CREDITS_PER_CALL)  # ~486/1k
                for i in ids:
                    yield str(i)
                    seen += 1
                    if max_items and seen >= max_items:
                        return
                if not has_next:
                    # DO NOT trust has_next_page blindly. The gateway intermittently reports
                    # has_next_page=False mid-list (it also throws sporadic 503s), and the old
                    # code returned right here — silently truncating the member and marking the
                    # crawl "done". That's how @JohnCena ended up with 55k of 1.06M following
                    # and @FerreWeb3 with 78% of his: no error, no retry, just missing edges.
                    # A stop is only believed if it's CONFIRMED by a re-request of the same
                    # cursor; a real end-of-list is stable, a lie usually isn't.
                    if next_cursor and next_cursor != cursor:
                        confirm = await self._get(
                            client, "/user/followings_ids",
                            {"userId": str(user_id), "pageSize": page, "cursor": next_cursor},
                        )
                        more = confirm.get("ids") or []
                        if more:
                            logger.warning(
                                "userId=%s: gateway said has_next_page=False at %s ids but the "
                                "next cursor still returns %s more — continuing (would have "
                                "truncated)", user_id, seen, len(more))
                            self.credits_spent += max(int(0.49 * len(more)),
                                                      _MIN_CREDITS_PER_CALL)
                            for i in more:
                                yield str(i)
                                seen += 1
                                if max_items and seen >= max_items:
                                    return
                            cursor = confirm.get("next_cursor") or ""
                            if not cursor:
                                return
                            continue
                    return
                if not next_cursor or next_cursor == cursor:
                    return
                cursor = next_cursor

    async def iter_following_profiles(
        self, username: str, *, max_items: int | None = None
    ) -> AsyncIterator[dict]:
        """Yield full profile dicts for accounts ``username`` follows.

        Terminates on ``has_next_page is False`` (the catalog's recommended, most
        reliable stop condition), with empty-page / missing-cursor as safety nets.
        """
        page = settings.crawl_profile_page_size
        per_item = _per_item_credits(_FOLLOWING_PROFILE_CREDITS, page)
        cursor, seen, empty_retries = "", 0, 0
        async with self._client() as client:
            while True:
                data = await self._get(
                    client, "/user/followings",
                    {"userName": username.lstrip("@"), "pageSize": page, "cursor": cursor},
                )
                users = data.get("followings") or []
                has_next = bool(data.get("has_next_page"))
                next_cursor = data.get("next_cursor") or ""

                if not users:
                    # Empty page. A 200-OK with no followings is the SNEAKY failure: if the API
                    # also says has_next_page=True, it's a transient glitch — NOT the end — so we
                    # re-fetch the SAME cursor a few times rather than silently truncating.
                    self.credits_spent += _MIN_CREDITS_PER_CALL
                    if has_next:
                        if empty_retries < _EMPTY_PAGE_RETRIES:
                            empty_retries += 1
                            await asyncio.sleep(min(8, 2 ** empty_retries))
                            continue  # retry the SAME page; do not advance the cursor
                        # still empty but API insists there's more -> refuse to truncate; raise so
                        # the member-level retry re-crawls from scratch (caught in crawl service).
                        raise RuntimeError(
                            f"@{username}: empty page with has_next_page=True after "
                            f"{_EMPTY_PAGE_RETRIES} retries — refusing to truncate"
                        )
                    return  # genuine end (has_next_page=False, no users)
                empty_retries = 0
                self.credits_spent += max(per_item * len(users), _MIN_CREDITS_PER_CALL)
                for u in users:
                    yield u
                    seen += 1
                    if max_items and seen >= max_items:
                        return
                if not has_next:
                    return
                # stop if the cursor doesn't advance — guards against an upstream that
                # returns has_next_page=True forever with a repeating/empty cursor.
                if not next_cursor or next_cursor == cursor:
                    return
                cursor = next_cursor

    # ---- search (engagement ingestion) -----------------------------------

    async def iter_search_tweets(
        self, *, query: str, since_id: str | None = None, max_pages: int = 5,
    ) -> AsyncIterator[dict]:
        """Yield advanced-search results (X operators), newest-first (``queryType=Latest``).

        The engagement tracker's source of truth: ``from:<user> include:nativeretweets``
        returns a member's COMPLETE outbound stream — replies to others, native retweets,
        quotes, originals — which ``/user/last_tweets`` silently drops replies from
        (verified 2026-07-02: identical results with includeReplies true/false).

        Incremental: SKIPS items with ``id <= since_id`` and stops paging only when an
        ENTIRE page is old — search ordering is only approximately newest-first, so a hard
        stop at the first old id (the ``iter_user_tweets`` timeline pattern) could truncate
        a page behind one out-of-order item and silently drop unseen tweets. Time operators
        like ``since:`` proved fuzzy — tweet-id skip + id-keyed dedup is exact.
        Response is flat: ``{tweets[], has_next_page, next_cursor}``. Verified proxied by
        the gateway. Cost 15 credits/tweet ($0.15/1k at 100k credits/USD), min 60/call.
        """
        cursor, pages = "", 0
        async with self._client() as client:
            while pages < max_pages:
                data = await self._get(client, "/tweet/advanced_search",
                                       {"query": query, "queryType": "Latest", "cursor": cursor})
                tweets = data.get("tweets") or []
                if not tweets:
                    return
                self.credits_spent += max(int(15 * len(tweets)), _MIN_CREDITS_PER_CALL)
                fresh_in_page = 0
                for t in tweets:
                    tid = str(t.get("id") or "")
                    if since_id and tid.isdigit() and int(tid) <= int(since_id):
                        continue  # already-seen item; keep scanning (order is approximate)
                    fresh_in_page += 1
                    yield t
                if since_id and fresh_in_page == 0:
                    return  # a whole page of already-seen ids -> genuinely caught up
                cursor = data.get("next_cursor") or ""
                pages += 1
                if not data.get("has_next_page") or not cursor:
                    return

    # ---- tweets (mindshare ingestion) ------------------------------------

    async def iter_user_tweets(
        self, *, user_id: str | None = None, username: str | None = None,
        since_id: str | None = None, max_pages: int = 5, include_replies: bool = False,
    ) -> AsyncIterator[dict]:
        """Yield a user's recent tweets (newest-first) from the gateway's ``/user/last_tweets``.

        Incremental: stops as soon as it reaches a tweet with ``id <= since_id`` (already ingested).
        Each tweet carries engagement (``likeCount``/``retweetCount``/``replyCount``/``quoteCount``/
        ``viewCount``) + ``author`` + ``entities`` (cashtags) — everything the mindshare scorer needs.
        Verified live: the loudrr gateway proxies this endpoint.
        """
        base = {"includeReplies": str(include_replies).lower()}
        if user_id:
            base["userId"] = str(user_id)
        elif username:
            base["userName"] = username.lstrip("@")
        else:
            raise ValueError("iter_user_tweets needs user_id or username")
        cursor, pages = "", 0
        async with self._client() as client:
            while pages < max_pages:
                data = await self._get(client, "/user/last_tweets", {**base, "cursor": cursor})
                box = data.get("data") or data           # last_tweets is data-wrapped
                tweets = box.get("tweets") or []
                if not tweets:
                    return
                # $0.15/1k tweets at 100k credits/USD = 15 credits/tweet (was booked 0.15 —
                # a 100x undercount that let budget guards trip ~100x late; fixed 2026-07-02)
                self.credits_spent += max(int(15 * len(tweets)), _MIN_CREDITS_PER_CALL)
                for t in tweets:
                    tid = str(t.get("id") or "")
                    if since_id and tid and tid.isdigit() and int(tid) <= int(since_id):
                        return  # reached already-seen territory (tweets are newest-first)
                    yield t
                # `next_cursor`/`has_next_page` live at the TOP LEVEL of the response, NOT inside
                # the data-wrapped box that holds `tweets` (verified live: box keys are just
                # pin_tweet+tweets). Reading them from `box` left cursor="" so paging died after
                # page 1 — silently truncating every ingest to ~20 tweets. Fixed 2026-07-23.
                cursor = data.get("next_cursor") or ""
                pages += 1
                if not data.get("has_next_page") or not cursor:
                    return
