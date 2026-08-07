"""Sorsa API client — CALIBRATION ONLY.

We are replicating Sorsa, not depending on it. This client exists solely to pull
ground-truth scores for a sample of accounts so we can calibrate our own scoring
(map our normalized PageRank onto Sorsa's thousands-scale, sanity-check category
counts, validate top-followers ordering).

Auth: ``ApiKey`` header. Base: ``https://api.sorsa.io/v3``. Flat 20 req/sec.
NOTE (2026-06-15): keys seen so far were unusable — prior key 403 "request limit
exceeded" (paid, quota spent); current key 403 "api key not payed" (UNPAID, no
balance). Confirm balance via ``key_usage_info()`` before harvesting.

Every call self-records its request + response SHAPE to ``data/tweetscout_calls.jsonl``
so we accumulate a study of their API structure (see docs/tweetscout_api_observations.md).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone

import httpx
from aiolimiter import AsyncLimiter

from app.core.config import settings

logger = logging.getLogger(__name__)

BASE_URL = "https://api.sorsa.io/v3"
_TIMEOUT = httpx.Timeout(30.0)
# Anchor state/log to the repo root, NOT the CWD — a resume from a different working
# directory must find the same files or the spend counter resets (review finding #11).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CALL_LOG = os.path.join(_REPO_ROOT, "data", "tweetscout_calls.jsonl")
_MAX_ATTEMPTS = 5


class SorsaBudgetExhausted(RuntimeError):
    """Raised by the client when a request would exceed the configured hard_ceiling —
    a local kill-switch so retry storms / bookend calls can't breach the 10k cap."""


def _log_shape(method: str, path: str, params: dict, status: int, body_text: str) -> None:
    """Append one request/response-shape record to the call log. Best-effort: logging
    must NEVER break an API call, so all errors here are swallowed."""
    try:
        rec: dict = {"ts": datetime.now(timezone.utc).isoformat(), "method": method,
                     "path": path, "params": params, "status": status}
        try:
            body = json.loads(body_text)
        except Exception:
            body = None
        if isinstance(body, dict):
            rec["resp_keys"] = {k: type(v).__name__ for k, v in body.items()}
            if "message" in body:
                rec["message"] = body["message"]
        elif body is None:
            rec["resp_kind"] = "non-json"
        os.makedirs(os.path.dirname(CALL_LOG), exist_ok=True)
        with open(CALL_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception:  # noqa: BLE001
        pass


class SorsaQuotaError(RuntimeError):
    """Raised when the key is valid but cannot serve requests (403): quota exhausted
    ('request limit exceeded') OR unpaid ('api key not payed'). Terminal — stop the run."""


class SorsaTransientError(RuntimeError):
    """429/5xx that survived all retries. Caller may skip the account, not the run."""


def _one_target(username: str | None, user_id: str | None, user_link: str | None) -> dict:
    """Exactly one of the three address params, mirroring Sorsa's one-of contract."""
    if user_id is not None:
        return {"user_id": str(user_id)}
    if username is not None:
        return {"username": username.lstrip("@")}
    if user_link is not None:
        return {"user_link": user_link}
    raise ValueError("one of username/user_id/user_link required")


class SorsaClient:
    """ONE shared instance per harvest run: it owns the rate limiter and the
    ``requests_spent`` counter the orchestrator uses to enforce the 10k ceiling."""

    def __init__(self, api_key: str | None = None, qps: int | None = None,
                 hard_ceiling: int | None = None, max_connections: int = 5):
        self.api_key = api_key if api_key is not None else settings.sorsa_key
        self._limiter = AsyncLimiter(qps or settings.sorsa_qps, 1)
        self.requests_spent = 0   # billable API calls (NOT key_usage_info or dashboard)
        self.hard_ceiling = hard_ceiling  # local kill-switch; None = disabled
        # Connection pool caps REAL throughput independently of qps: Sorsa's latency is ~3s,
        # so 5 connections => ~1.6 req/s no matter what the rate limiter allows. Bulk harvests
        # raise this (measured safe: ~10/s; 429s start around 25 concurrent).
        _limits = httpx.Limits(max_connections=max_connections,
                               max_keepalive_connections=max_connections)
        # ONE keep-alive client reused for all api.sorsa.io calls (no TLS handshake per
        # call) + a separate one for the app.sorsa.io dashboard host (lazy).
        self._http = httpx.AsyncClient(
            base_url=BASE_URL, timeout=_TIMEOUT, headers={"ApiKey": self.api_key},
            limits=_limits,
        )
        self._dash: httpx.AsyncClient | None = None

    async def aclose(self) -> None:
        await self._http.aclose()
        if self._dash is not None:
            await self._dash.aclose()

    def _retry_after(self, resp: httpx.Response, attempt: int) -> float:
        """Honor Retry-After (numeric seconds OR HTTP-date), else exponential backoff."""
        ra = resp.headers.get("Retry-After")
        if ra:
            if ra.replace(".", "", 1).isdigit():
                return float(ra)
            try:  # HTTP-date form
                from email.utils import parsedate_to_datetime
                from datetime import datetime, timezone
                dt = parsedate_to_datetime(ra)
                return max(0.0, (dt - datetime.now(timezone.utc)).total_seconds())
            except Exception:  # noqa: BLE001
                pass
        return min(2 ** attempt, 30)

    async def _request(self, path: str, params: dict) -> dict:
        """Rate-limited GET with retry on 429/5xx (honors Retry-After). Counts EVERY
        billable attempt against ``requests_spent``. Raises SorsaQuotaError (terminal,
        unpaid/quota-spent), SorsaBudgetExhausted (would breach hard_ceiling), or
        SorsaTransientError (retries exhausted OR any unexpected non-quota 4xx — caller
        should SKIP that account, never abort the run)."""
        for attempt in range(_MAX_ATTEMPTS):
            if self.hard_ceiling is not None and self.requests_spent >= self.hard_ceiling:
                raise SorsaBudgetExhausted(f"hard_ceiling {self.hard_ceiling} reached")
            await self._limiter.acquire()
            # Count BEFORE the call: a ReadTimeout/conn-reset may still have billed the
            # request server-side, so incrementing first keeps us from UNDER-counting the
            # irreversible budget (errs toward under-spend; server reconcile corrects drift).
            self.requests_spent += 1
            try:
                resp = await self._http.get(path, params=params)
            except httpx.TransportError as e:  # timeout / connection reset -> transient
                wait = min(2 ** attempt, 30)
                logger.warning("sorsa transport error on %s: %s -> retry %d/%d in %.1fs",
                               path, e, attempt + 1, _MAX_ATTEMPTS, wait)
                await asyncio.sleep(wait)
                continue
            _log_shape("GET", path, params, resp.status_code, resp.text)
            if resp.status_code == 403:
                try:
                    msg = resp.json().get("message", resp.text[:120])
                except Exception:  # noqa: BLE001
                    msg = resp.text[:120]
                if "payed" in msg or "limit" in msg:
                    raise SorsaQuotaError(msg)          # terminal: stop the whole run
                raise SorsaTransientError(f"403 {msg}")  # forbidden/protected: skip account
            if resp.status_code == 429 or resp.status_code >= 500:
                wait = self._retry_after(resp, attempt)
                logger.warning("sorsa %s on %s -> retry %d/%d in %.1fs",
                               resp.status_code, path, attempt + 1, _MAX_ATTEMPTS, wait)
                await asyncio.sleep(wait)
                continue
            if resp.status_code >= 400:
                raise SorsaTransientError(f"{resp.status_code} on {path}")  # skip account
            return resp.json()
        raise SorsaTransientError(f"{path}: exhausted {_MAX_ATTEMPTS} retries")

    async def _get(self, path: str, username: str | None = None, *,
                   user_id: str | None = None, user_link: str | None = None) -> dict:
        return await self._request(path, _one_target(username, user_id, user_link))

    async def score(self, username: str | None = None, *, user_id: str | None = None) -> float:
        return (await self._get("/score", username, user_id=user_id)).get("score", 0.0)

    async def score_changes(self, username: str | None = None, *, user_id: str | None = None) -> dict:
        return await self._get("/score-changes", username, user_id=user_id)

    async def followers_stats(self, username: str | None = None, *, user_id: str | None = None) -> dict:
        return await self._get("/followers-stats", username, user_id=user_id)

    async def top_followers(self, username: str | None = None, *, user_id: str | None = None) -> list[dict]:
        return (await self._get("/top-followers", username, user_id=user_id)).get("users", [])

    async def top_following(self, username: str | None = None, *, user_id: str | None = None) -> list[dict]:
        return (await self._get("/top-following", username, user_id=user_id)).get("users", [])

    async def info_batch(self, usernames: list[str] | None = None,
                         user_ids: list[str] | None = None) -> list[dict]:
        """GET /info-batch — resolve up to ~100 handles/ids -> profiles in ONE request.
        Uses REPEATED query params (?usernames=a&usernames=b) — verified live 2026-06-15
        (comma-joined resolves only the first). Normalizes each profile to carry both
        `id` (str) and `username` (lowercased from username|userName)."""
        params: dict = {}
        if usernames:
            params["usernames"] = [u.lstrip("@") for u in usernames[:100]]
        if user_ids:
            params["user_ids"] = [str(i) for i in user_ids[:100]]
        if not params:
            return []
        users = (await self._request("/info-batch", params)).get("users", [])
        for u in users:
            if u.get("username") is None and u.get("userName") is not None:
                u["username"] = u["userName"]
            if u.get("id") is not None:
                u["id"] = str(u["id"])
        return users

    async def probe_top_followers_score(self, username: str = "cz_binance") -> dict:
        """Settle the research linchpin: does /top-followers actually return a usable
        per-account numeric score? (Their docs are self-contradictory.) Returns the
        first entry + a verdict so we know whether to trust top-follower score labels
        or fall back to per-account /score calls. Run once when the key is live."""
        users = await self.top_followers(username)
        if not users:
            return {"usable": False, "reason": "no top-followers returned", "sample": None}
        first = users[0]
        sc = first.get("score")
        usable = isinstance(sc, (int, float)) and not isinstance(sc, bool)
        return {
            "usable": usable,
            "score_field_value": sc,
            "all_fields": sorted(first.keys()),
            "sample": first,
            "note": "if usable=False, harvest labels via direct /score per discovered account",
        }

    async def dashboard_score(self, handle: str) -> dict | None:
        """FREE read (app.sorsa.io, no ApiKey -> does NOT count against the 10k API
        budget) of the thousands-scale score + Tier from the public profile page.
        Tries the Next.js RSC/flight JSON payload first, then a visible Tier+thousands
        regex. Returns None on gated/placeholder pages (Loading/Upgrade/0). Pace slowly
        (Cloudflare). VERIFY the parse against a live page once the key is funded."""
        import re

        if self._dash is None:
            self._dash = httpx.AsyncClient(
                timeout=_TIMEOUT, follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                         "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"},
                limits=httpx.Limits(max_connections=2, max_keepalive_connections=2),
            )
        url = f"https://app.sorsa.io/profile/{handle.lstrip('@')}"
        try:
            await self._limiter.acquire()   # pace dashboard reads too (Cloudflare-friendly)
            resp = await self._dash.get(url)
            html = resp.text
            _log_shape("GET", "app.sorsa.io/profile", {"handle": handle}, resp.status_code, html)
        except httpx.HTTPError as e:
            logger.warning("dashboard fetch %s failed: %s", handle, e)
            return None

        score: float | None = None
        # strategy 1: numeric score embedded in the RSC/flight or hydration JSON
        for pat in (r'\\?"score\\?"\s*:\s*(\d{2,5})(?:\.\d+)?',
                    r'"tweetscoutScore"\s*:\s*(\d{2,5})'):
            m = re.search(pat, html)
            if m:
                score = float(m.group(1))
                break
        # strategy 2: visible thousands number rendered in the page body
        if score is None:
            m = re.search(r">\s*(\d{3,5})\s*<", html)
            score = float(m.group(1)) if m else None

        if not score:  # missing or 0 -> gated/placeholder page, unusable
            return None
        m_tier = re.search(r"Tier\s*([1-5])[.\s]*([A-Za-z]+)", html)
        return {"score": score,
                "tier": int(m_tier.group(1)) if m_tier else None,
                "tier_name": m_tier.group(2) if m_tier else None}

    async def key_usage_info(self) -> dict:
        """GET /key-usage-info — balance/quota: {key_requests, remaining_requests,
        total_requests, valid_until}. 403s when unpaid or fully exhausted. Observed
        live (2026-06-15) to NOT decrement quota (key_requests stayed 0 after it), so
        it's treated as FREE — not counted against requests_spent / the ceiling, and
        safe to call for spend reconciliation. The server's `key_requests` is the
        authoritative cumulative-spend ledger."""
        await self._limiter.acquire()
        resp = await self._http.get("/key-usage-info")
        _log_shape("GET", "/key-usage-info", {}, resp.status_code, resp.text)
        if resp.status_code == 403:
            try:
                msg = resp.json().get("message", resp.text[:120])
            except Exception:  # noqa: BLE001
                msg = resp.text[:120]
            raise SorsaQuotaError(msg)
        resp.raise_for_status()
        return resp.json()
