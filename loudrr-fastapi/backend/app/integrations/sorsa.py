"""Sorsa score provider — reads what Sorsa shows a logged-out visitor, nothing gated.

Sorsa (sorsa.io, formerly TweetScout) scores crypto X accounts on the same
thousands scale our tier bands were built for (e.g. 0xBlest_ ≈ 590 -> Based),
so the score feeds ``tier.tier_for`` with no rescaling.

Data comes from the two anonymous calls Sorsa's public profile page
(``app.sorsa.io/profile/<handle>``) itself makes:

  * ``GET api.sorsa.io/v1/accounts/search?query=<handle>`` — the account
    object: score_value, followers, avatar, bio, ... (exact, case-insensitive
    handle match). Accounts Sorsa hasn't indexed answer ``400 please first
    login`` — the same wall the page shows — and come back as None. If the API
    answers in an unexpected way, the page's server-rendered flight data is
    parsed instead (slower, same object);
  * ``GET api.sorsa.io/v1/accounts/<id>/followers-info`` — the "Top followers"
    panel: smart-follower totals per category (influencer / project / fund)
    plus the top five of each.

Fields the page paywalls (bot-follower stats, the full follower list) are
never surfaced.

Politeness + failure policy. Nothing here raises; None means "no score now",
and ``last_outcome`` says why: "not_indexed" (Sorsa doesn't know the account)
or "unavailable" (our side, or Sorsa pushing back). Callers must never record
an unavailable miss as "no score":

  * every request leaves through the SCRAPE_PROXIES pool; no pool ->
    unavailable. We never scrape from the server's own IP;
  * request starts are spaced MIN_INTERVAL_S apart per process;
  * when Sorsa pushes back (Cloudflare challenge, 403, 429) the circuit opens
    for BLOCK_COOLDOWN_S and every call is unavailable until it closes. We back
    off — we do NOT hop to another proxy to get around a block. Only
    connection-level failures (dead proxy, timeout) retry on another proxy;
  * found / not-indexed results are cached per handle for a few minutes — only
    to absorb near-simultaneous fetches (a double-tapped Refresh). Scores are
    fetched at sign-up and on the user's Refresh tap; the Refresh cooldown is
    longer than this cache, so an allowed tap is fresh.

The returned dict mirrors ``LoudrrAnalyticsClient.get_user_data`` (the flat
TweetScout shape ``services.users._profile_values`` reads), plus
``smart_followers`` (int total) and ``top_followers`` (list, best first).
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import random
import re
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import quote, urlencode

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

SEARCH_URL = "https://api.sorsa.io/v1/accounts/search"
PROFILE_URL = "https://app.sorsa.io/profile/{handle}"
FOLLOWERS_INFO_URL = "https://api.sorsa.io/v1/accounts/{account_id}/followers-info"

MIN_INTERVAL_S = 1.0          # spacing between request starts, per process
BLOCK_COOLDOWN_S = 15 * 60    # how long we stay away after Sorsa pushes back
CACHE_TTL_S = 10 * 60         # dedupe window only — keep it under the Refresh cooldown
_CACHE_MAX = 5000
_CONNECT_ATTEMPTS = 2         # proxies tried per request, on connection errors only

_TIMEOUT = httpx.Timeout(15.0)
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

_HANDLE_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")
_UUID_RE = re.compile(r"^[0-9a-fA-F-]{36}$")
# Next.js streams the page's data as JS string literals: self.__next_f.push([1,"..."])
_FLIGHT_RE = re.compile(r'self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)', re.S)
# the account object carries "screen_name":"<handle>" as a top-level key
_SCREEN_NAME_RE = re.compile(r'"screen_name"\s*:\s*"([A-Za-z0-9_]{1,15})"')
_MAX_OBJECT_PROBES = 50
# X banner URLs embed the owner's numeric user id: /profile_banners/<id>/<ts>
_BANNER_ID_RE = re.compile(r"/profile_banners/(\d+)/")
_NOT_INDEXED_MARK = "in our base yet"


# ---- module state (per process): pacing, circuit breaker, cache ----
_MISS = object()
_cache: dict[str, tuple[float, str, Optional[dict]]] = {}
_blocked_until = 0.0
_next_slot = 0.0
_pace_gate: Optional[tuple[asyncio.AbstractEventLoop, asyncio.Lock]] = None
_warned_no_proxies = False


def reset_state() -> None:
    """Forget cache, circuit and pacing (tests)."""
    global _blocked_until, _next_slot, _pace_gate, _warned_no_proxies
    _cache.clear()
    _blocked_until = 0.0
    _next_slot = 0.0
    _pace_gate = None
    _warned_no_proxies = False


def is_blocked() -> bool:
    return time.monotonic() < _blocked_until


def _open_circuit(reason: str) -> None:
    global _blocked_until
    _blocked_until = time.monotonic() + BLOCK_COOLDOWN_S
    logger.warning(
        "Sorsa pushed back (%s) — pausing all Sorsa requests for %d min",
        reason, BLOCK_COOLDOWN_S // 60,
    )


def _cache_get(key: str):
    """(outcome, data) or _MISS."""
    hit = _cache.get(key)
    if hit is None or hit[0] < time.monotonic():
        return _MISS
    # a copy: callers keep the payload (e.g. x_profiles.raw_tweetscout_data)
    return hit[1], copy.deepcopy(hit[2])


def _cache_put(key: str, outcome: str, value: Optional[dict]) -> None:
    if len(_cache) >= _CACHE_MAX:
        now = time.monotonic()
        for k in [k for k, (exp, _o, _v) in _cache.items() if exp < now]:
            del _cache[k]
        if len(_cache) >= _CACHE_MAX:
            _cache.clear()
    _cache[key] = (time.monotonic() + CACHE_TTL_S, outcome, copy.deepcopy(value))


async def _wait_turn(interval: float) -> None:
    """Reserve the next request slot, spaced `interval` apart. Only the slot
    bookkeeping is locked — requests themselves still run concurrently."""
    global _next_slot, _pace_gate
    if interval <= 0:
        return
    loop = asyncio.get_running_loop()
    if _pace_gate is None or _pace_gate[0] is not loop:
        _pace_gate = (loop, asyncio.Lock())
    async with _pace_gate[1]:
        now = time.monotonic()
        start = max(now, _next_slot)
        _next_slot = start + interval
    if start > now:
        await asyncio.sleep(start - now)


# ---- proxy pool ----
def parse_proxies(raw: str) -> list[str]:
    """Webshare ``host:port:user:pass`` tokens (or full URLs) -> httpx proxy URLs."""
    out: list[str] = []
    for token in re.split(r"[\s,;]+", raw or ""):
        if not token:
            continue
        if "://" in token:
            out.append(token)
            continue
        parts = token.split(":")
        if len(parts) == 4:
            host, port, user, password = parts
            out.append(f"http://{quote(user, safe='')}:{quote(password, safe='')}@{host}:{port}")
    return out


def load_proxy_pool() -> list[str]:
    if settings.scrape_proxies.strip():
        return parse_proxies(settings.scrape_proxies)
    path = Path(settings.scrape_proxy_file) if settings.scrape_proxy_file else None
    if path is not None and path.is_file():
        return parse_proxies(path.read_text(encoding="utf-8"))
    return []


# ---- parsing (pure) ----
def _flight_text(html: str) -> str:
    """The plain text the page's Next.js flight chunks encode, concatenated."""
    parts = []
    for m in _FLIGHT_RE.finditer(html):
        try:
            parts.append(json.loads('"' + m.group(1) + '"'))
        except ValueError:
            continue
    return "".join(parts)


def parse_profile_page(html: str, handle: str) -> Optional[dict]:
    """The account object Sorsa server-rendered for `handle`, or None (not
    indexed, or the page layout changed).

    Finds each "screen_name":"<handle>" and walks back to the nearest "{"
    that decodes to an object holding that key at top level — independent of
    key order, and braces/quotes inside strings can't fool it (raw_decode
    parses real JSON)."""
    text = _flight_text(html)
    decoder = json.JSONDecoder()
    wanted = handle.lower()
    for m in _SCREEN_NAME_RE.finditer(text):
        if m.group(1).lower() != wanted:
            continue
        start = text.rfind("{", 0, m.start())
        for _ in range(_MAX_OBJECT_PROBES):
            if start == -1:
                break
            try:
                obj, _end = decoder.raw_decode(text, start)
            except ValueError:
                obj = None
            if isinstance(obj, dict) and str(obj.get("screen_name") or "").lower() == wanted:
                if "score_value" in obj:
                    return obj
                break  # the object owning this screen_name has no score
            start = text.rfind("{", 0, start)
    return None


def parse_followers_info(data) -> tuple[Optional[int], list[dict]]:
    """(total smart followers, top smart followers best-first) from the
    followers-info payload. (None, []) when the payload isn't recognised."""
    groups = data.get("followers") if isinstance(data, dict) else None
    if not isinstance(groups, dict):
        return None, []
    total = 0
    seen: set[str] = set()
    top: list[dict] = []
    for group in groups.values():
        if not isinstance(group, dict):
            continue
        count = group.get("total")
        if isinstance(count, int) and count > 0:
            total += count
        for acc in group.get("accounts") or []:
            if not isinstance(acc, dict):
                continue
            username = str(acc.get("screen_name") or "").lstrip("@")
            if not username or username.lower() in seen:
                continue
            seen.add(username.lower())
            top.append({
                "username": username,
                "name": acc.get("name") or "",
                "avatar": acc.get("avatar") or "",
                "score": _float(acc.get("score_value")),
            })
    top.sort(key=lambda a: a["score"] or 0.0, reverse=True)
    return total, top


def _float(value) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _register_date(value) -> Optional[str]:
    """Unix seconds -> "YYYY-MM-DD" (the format _parse_register_date reads)."""
    try:
        ts = int(value)
    except (TypeError, ValueError):
        return None
    if ts <= 0:
        return None
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
    except (OverflowError, OSError, ValueError):
        return None


def _score_payload(account: dict, handle: str) -> Optional[dict]:
    score = _float(account.get("score_value"))
    if score is None:
        return None
    banner = str(account.get("banner") or "")
    x_id = _BANNER_ID_RE.search(banner)
    return {
        # the numeric X id when the banner URL gives it away; "" otherwise
        # (_upsert_x_profile never blanks an existing id)
        "id": x_id.group(1) if x_id else "",
        "screen_name": account.get("screen_name") or handle,
        "name": account.get("name") or "",
        "description": account.get("description") or "",
        "followers_count": _int(account.get("followers")),
        "friends_count": _int(account.get("friends")),
        "tweets_count": _int(account.get("tweets")),
        "score": score,
        "avatar": account.get("avatar") or "",
        "banner": banner,
        "verified": False,
        "can_dm": False,
        "register_date": _register_date(account.get("register_date")),
        "category": account.get("category") or "",
        "sorsa_id": str(account.get("id") or ""),
        "smart_followers": None,
        "top_followers": None,
    }


def _pushback(resp: httpx.Response) -> Optional[str]:
    if resp.headers.get("cf-mitigated"):
        return f"HTTP {resp.status_code} Cloudflare {resp.headers['cf-mitigated']}"
    if resp.status_code in (403, 429):
        return f"HTTP {resp.status_code}"
    return None


def _default_client(proxy: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        proxy=proxy, timeout=_TIMEOUT, follow_redirects=True, headers=_HEADERS,
    )


class SorsaClient:
    def __init__(
        self,
        proxies: Optional[list[str]] = None,
        *,
        client_factory: Callable[[str], httpx.AsyncClient] = _default_client,
        min_interval_s: float = MIN_INTERVAL_S,
    ):
        self._proxies = load_proxy_pool() if proxies is None else proxies
        self._client_factory = client_factory
        self._min_interval_s = min_interval_s
        # why the last get_user_data returned what it did:
        # "found" | "not_indexed" | "unavailable"
        self.last_outcome: Optional[str] = None

    @staticmethod
    def is_blocked() -> bool:
        return is_blocked()

    def is_unavailable(self) -> bool:
        """The last miss was on our side (pushback, proxies, odd response),
        not Sorsa lacking the account — don't record it as "no score"."""
        return self.last_outcome == "unavailable" or is_blocked()

    async def _get(self, url: str) -> Optional[httpx.Response]:
        """One polite GET through the pool. None when blocked or unreachable."""
        for proxy in random.sample(self._proxies, min(_CONNECT_ATTEMPTS, len(self._proxies))):
            await _wait_turn(self._min_interval_s)
            if is_blocked():
                return None
            try:
                async with self._client_factory(proxy) as client:
                    resp = await client.get(url)
            except httpx.TransportError as e:
                # dead proxy / timeout — the one case we try another proxy
                logger.warning("Sorsa request failed via a proxy (%s) — %s", type(e).__name__, url)
                continue
            reason = _pushback(resp)
            if reason:
                _open_circuit(reason)
                return None
            return resp
        return None

    async def _lookup_account(self, handle: str) -> tuple[Optional[dict], str]:
        """(account object, outcome) via the search API the public page uses;
        the page itself is only parsed when the API answers unexpectedly."""
        resp = await self._get(f"{SEARCH_URL}?{urlencode({'query': handle})}")
        if resp is None:
            return None, "unavailable"
        try:
            body = resp.json()
        except ValueError:
            body = None
        if resp.status_code == 200 and isinstance(body, dict) and body.get("screen_name"):
            if str(body["screen_name"]).lower() == handle.lower():
                return body, "found"
            return None, "not_indexed"  # the exact-match API answered for another handle
        if (
            resp.status_code in (400, 404)
            and isinstance(body, dict)
            and "login" in str(body.get("error") or "").lower()
        ):
            return None, "not_indexed"  # "please first login" — the page's wall

        logger.warning(
            "Sorsa search API HTTP %s for @%s — falling back to the page", resp.status_code, handle,
        )
        page = await self._get(PROFILE_URL.format(handle=handle))
        if page is None or page.status_code != 200:
            return None, "unavailable"
        account = parse_profile_page(page.text, handle)
        if account is not None:
            return account, "found"
        if _NOT_INDEXED_MARK in page.text:
            return None, "not_indexed"
        logger.error("Sorsa page for @%s had no account data — page layout changed?", handle)
        return None, "unavailable"

    async def get_user_data(self, username: str) -> Optional[dict]:
        """Score + profile + smart followers for `username`, or None (see
        ``last_outcome`` for why)."""
        global _warned_no_proxies
        self.last_outcome = None
        handle = (username or "").strip().lstrip("@")
        if not _HANDLE_RE.match(handle):
            self.last_outcome = "not_indexed"  # not a valid X handle: never scorable
            return None
        key = handle.lower()
        cached = _cache_get(key)
        if cached is not _MISS:
            self.last_outcome, data = cached
            return data
        if is_blocked():
            self.last_outcome = "unavailable"
            return None
        if not self._proxies:
            if not _warned_no_proxies:
                logger.error(
                    "Sorsa score provider has no proxy pool (SCRAPE_PROXIES / "
                    "SCRAPE_PROXY_FILE) — scores are unavailable until one is set",
                )
                _warned_no_proxies = True
            self.last_outcome = "unavailable"
            return None

        account, outcome = await self._lookup_account(handle)
        data = _score_payload(account, handle) if account is not None else None
        if data is None:
            # an indexed account without a numeric score counts as not indexed
            self.last_outcome = "not_indexed" if outcome == "found" else outcome
            if self.last_outcome == "not_indexed":
                _cache_put(key, "not_indexed", None)
            return None

        if _UUID_RE.match(data["sorsa_id"]):
            info = await self._get(FOLLOWERS_INFO_URL.format(account_id=data["sorsa_id"]))
            if info is not None and info.status_code == 200:
                try:
                    total, top = parse_followers_info(info.json())
                except ValueError:
                    total, top = None, []
                if total is not None:
                    data["smart_followers"] = total
                    data["top_followers"] = top

        self.last_outcome = "found"
        _cache_put(key, "found", data)
        return data

    async def get_top_followers(self, username: str, k: int = 10) -> Optional[list[dict]]:
        """Top smart followers ({"username", "name", "avatar", "score"}, best
        first) — the same fetch as get_user_data, served from its cache."""
        data = await self.get_user_data(username)
        if data is None or data.get("top_followers") is None:
            return None
        return data["top_followers"][:k]
