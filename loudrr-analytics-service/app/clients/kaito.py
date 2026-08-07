"""Kaito mindshare-arena client(s).

**Key finding (2026-06-30):** the data API ``hub.kaito.ai/api/v1/voices/*`` is **not**
Cloudflare-challenged — only the ``kaito.ai`` HTML pages are. The JSON endpoints return data to
a plain HTTPS request that carries browser-like headers + a Chrome TLS fingerprint. So the fast
default path is **``KaitoHTTPClient``** (curl_cffi + rotating proxies, no browser). Datacenter
(Webshare) proxies are fine precisely because there is no CF challenge to fail, and there is no
proof-of-work on ``/voices/*`` (PoW only gates the legacy ``/kol|/tickers`` paths).

``KaitoClient`` (Camoufox, solves Cloudflare) is kept as a fallback for the day they gate the API.

Endpoint taxonomy (verified live):

    GET /voices/{vertical}/{endpoint}?sector=&duration=&limit=    (limit caps at 100)

  * vertical:  crypto | ai | equity(companies "Trading") | trading(voices "Trading") | preipo
  * kind 'companies' uses the ``company_``-prefixed endpoints; 'voices' the bare names.
  * sector  = sub-section (ALL + e.g. crypto's PRETGE/INFOMKT/EXCHANGE, TopicPerpDEX/PredictionMarkets)
  * duration (leaderboards) ∈ {24h,7d,30d,3m,6m,12m}  (48h/72h only exist as delta_all columns)
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from curl_cffi import requests as cffi

from app.core.config import settings

logger = logging.getLogger(__name__)

# kind 'companies' calls the company_-prefixed endpoints; 'voices' the bare ones.
_COMPANY_PREFIX = "company_"
# The "Trading" vertical is named differently per kind on Kaito's API.
_VERTICAL_ALIASES = {("companies", "trading"): "equity", ("voices", "equity"): "trading"}

# Browser-like headers — the API serves JSON to these + a Chrome TLS profile (no auth/cookie).
_API_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    "Origin": "https://kaito.ai",
    "Referer": "https://kaito.ai/",
    "Accept": "application/json, text/plain, */*",
}

# Endpoint families (suffix appended after the kind prefix for companies).
ENDPOINTS = {
    "sector_leaderboard": {"limit": 100},        # ranked leaderboard (main signal / smart-set roster)
    "mindshare_heatmap": {"top_n": 100},         # treemap weights
    "mindshare_delta_all": {"limit": 15},        # biggest movers (rich per-window history)
    "mindshare_ratio_all": {},                   # share ratios across windows
    "followers_change": {"type": "sf", "limit": 100},   # smart-follower gainers
    "mindshare_language": {"language": "en", "limit": 100},
}
# Endpoints whose params are (sector, duration); the *_all family takes sector only.
_SECTOR_DURATION_EPS = {"sector_leaderboard", "mindshare_heatmap", "mindshare_language", "followers_change"}
_SECTOR_ONLY_EPS = {"mindshare_delta_all", "mindshare_ratio_all"}

# Enabled sub-section codes per (kind, vertical), from the bundle's HISTORICAL_SECTORS map.
SECTORS = {
    ("companies", "crypto"): ["ALL", "PRETGE", "INFOMKT", "EXCHANGE"],
    ("companies", "ai"): ["ALL", "foundation_model", "coding_agents", "image_gen", "video_gen", "robotics", "vibe_apps"],
    ("companies", "trading"): ["ALL"],
    ("voices", "crypto"): ["ALL", "TopicPerpDEX", "PredictionMarkets"],
    ("voices", "ai"): ["ALL", "foundation_model", "coding_agents", "image_gen", "video_gen", "robotics", "vibe_apps"],
    ("voices", "trading"): ["ALL"],
}
# Leaderboard time windows (verified; 48h/72h are delta_all columns, not query durations).
DURATIONS = ["24h", "7d", "30d", "3m", "6m", "12m"]
VERTICALS = ["crypto", "ai", "trading"]
LEADERBOARD_LIMIT_MAX = 100  # the API rejects limit > 100; full roster = union across durations


class _EndpointMixin:
    """Shared endpoint-path / param building. Subclasses implement ``get_json``."""

    api_base: str

    def _endpoint_path(self, kind: str, vertical: str, endpoint: str) -> str:
        vert = _VERTICAL_ALIASES.get((kind, vertical), vertical)
        name = (_COMPANY_PREFIX + endpoint) if kind == "companies" else endpoint
        return f"/voices/{vert}/{name}"

    async def get_json(self, path: str, params: dict | None = None) -> Any:  # pragma: no cover
        raise NotImplementedError

    async def fetch(self, kind: str, vertical: str, endpoint: str, *,
                    sector: str = "ALL", duration: str = "7d", **extra) -> Any:
        params = dict(ENDPOINTS.get(endpoint, {}))
        params.update(extra)
        if endpoint in _SECTOR_DURATION_EPS:
            params.setdefault("sector", sector)
            params.setdefault("duration", duration)
        elif endpoint in _SECTOR_ONLY_EPS:
            params.setdefault("sector", sector)
        return await self.get_json(self._endpoint_path(kind, vertical, endpoint), params)

    async def leaderboard(self, kind: str, vertical: str, *, sector: str = "ALL",
                          duration: str = "7d", limit: int = 100) -> list[dict]:
        data = await self.fetch(kind, vertical, "sector_leaderboard",
                                sector=sector, duration=duration, limit=min(limit, LEADERBOARD_LIMIT_MAX))
        return data if isinstance(data, list) else (data.get("data") or [])


def _parse_proxy_lines(lines) -> list[str]:
    """Accept Webshare ``host:port:user:pass`` or full ``http://…`` lines -> proxy URLs."""
    out: list[str] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if line.startswith(("http://", "https://", "socks5://")):
            out.append(line)
        else:
            parts = line.split(":")
            if len(parts) == 4:
                h, port, u, pw = parts
                out.append(f"http://{u}:{pw}@{h}:{port}")
    return out


def _load_proxies(path: str) -> list[str]:
    """Proxies from a file, or — when the file is absent (e.g. in a container, since data/ is
    gitignored) — from the ``KAITO_PROXIES`` env/setting (newline/comma/semicolon separated)."""
    p = Path(path) if path else None
    if p and p.exists():
        return _parse_proxy_lines(p.read_text(encoding="utf-8").splitlines())
    inline = settings.kaito_proxies
    if inline:
        return _parse_proxy_lines(inline.replace(",", "\n").replace(";", "\n").splitlines())
    return []


class KaitoHTTPClient(_EndpointMixin):
    """Fast default reader: curl_cffi (Chrome TLS impersonation) + rotating proxies, no browser.

        async with KaitoHTTPClient() as kc:
            rows = await kc.leaderboard("voices", "crypto", sector="TopicPerpDEX", duration="7d")

    Concurrency is capped by a semaphore; each call rotates to the next proxy; 429/5xx/network
    retry on a fresh proxy; 400 (bad params) raises immediately (it's our request, not transient).
    """

    def __init__(self, *, proxy_file: str | None = None, proxies: list[str] | None = None,
                 concurrency: int | None = None, impersonate: str | None = None,
                 max_retries: int | None = None, api_base: str | None = None) -> None:
        self.api_base = (api_base or settings.kaito_api_base).rstrip("/")
        self.proxies = proxies if proxies is not None else _load_proxies(
            proxy_file or settings.kaito_proxy_file)
        self.impersonate = impersonate or settings.kaito_impersonate
        self.max_retries = max_retries or settings.kaito_max_retries
        self._pause = settings.kaito_request_pause_ms / 1000.0
        self._sem = asyncio.Semaphore(concurrency or settings.kaito_concurrency)
        self._i = 0

    async def __aenter__(self) -> "KaitoHTTPClient":
        logger.info("kaito http client: %d proxies, concurrency=%d",
                    len(self.proxies), self._sem._value)
        return self

    async def __aexit__(self, *exc) -> None:
        return None

    def _next_proxy(self) -> str | None:
        if not self.proxies:
            return None
        proxy = self.proxies[self._i % len(self.proxies)]
        self._i += 1
        return proxy

    def _blocking_get(self, url: str, params: dict, proxy: str | None) -> tuple[int, str]:
        pr = {"http": proxy, "https": proxy} if proxy else None
        r = cffi.get(url, params=params, impersonate=self.impersonate,
                     headers=_API_HEADERS, timeout=30, proxies=pr)
        return r.status_code, r.text

    async def get_json(self, path: str, params: dict | None = None) -> Any:
        url = path if path.startswith("http") else f"{self.api_base}{path}"
        last = ""
        async with self._sem:
            for attempt in range(1, self.max_retries + 1):
                proxy = self._next_proxy()
                try:
                    status, text = await asyncio.to_thread(self._blocking_get, url, params or {}, proxy)
                except Exception as e:  # noqa: BLE001 - network/proxy error -> rotate + retry
                    last = f"net:{type(e).__name__}"
                    await asyncio.sleep(min(4, attempt))
                    continue
                if status == 200:
                    if self._pause:
                        await asyncio.sleep(self._pause)
                    try:
                        return json.loads(text)
                    except json.JSONDecodeError as e:
                        raise RuntimeError(f"kaito non-JSON {url}: {text[:160]}") from e
                if status in (400, 404):  # definitive client errors (bad params / no such endpoint
                    raise RuntimeError(f"kaito {status} {url}: {text[:160]}")   # for this vertical) — don't retry
                last = f"http {status}: {text[:120]}"          # 429/5xx -> rotate proxy + retry
                await asyncio.sleep(min(5, attempt))
        raise RuntimeError(f"kaito: exhausted {self.max_retries} retries {url}: {last}")


class CloudflareSolveError(RuntimeError):
    """Raised when the Cloudflare managed challenge could not be cleared in time."""


class KaitoClient(_EndpointMixin):
    """Fallback reader: Camoufox solves Cloudflare, requests go through the browser session.

    Only needed if Kaito ever puts the API host behind the JS challenge. For the data pull today,
    prefer ``KaitoHTTPClient`` (no browser, faster, parallel).
    """

    WARM_URL = "https://kaito.ai/mindshare-arena/companies?vertical=crypto"

    def __init__(self, *, proxy: str | None = None, headless: bool | None = None,
                 api_base: str | None = None, launch_attempts: int = 3) -> None:
        self.proxy = proxy if proxy is not None else settings.kaito_proxy
        self.headless = headless if headless is not None else settings.kaito_headless
        self.api_base = (api_base or settings.kaito_api_base).rstrip("/")
        self.launch_attempts = launch_attempts
        self._cm = None
        self._browser = None
        self.page = None

    async def _launch(self) -> None:
        from camoufox.async_api import AsyncCamoufox
        opts: dict[str, Any] = {
            "headless": self.headless, "humanize": True, "geoip": True, "os": "windows",
            "window": (settings.kaito_window_w, settings.kaito_window_h),
        }
        if self.proxy:
            opts["proxy"] = {"server": self.proxy}
        self._cm = AsyncCamoufox(**opts)
        self._browser = await self._cm.__aenter__()
        self.page = await self._browser.new_page()

    async def _teardown(self) -> None:
        if self._cm is not None:
            try:
                await self._cm.__aexit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass
        self._cm = self._browser = self.page = None

    async def __aenter__(self) -> "KaitoClient":
        for attempt in range(1, self.launch_attempts + 1):
            await self._launch()
            try:
                await self._solve_cloudflare()
                return self
            except CloudflareSolveError:
                logger.warning("kaito: CF solve failed (%s/%s) — relaunching", attempt, self.launch_attempts)
                await self._teardown()
        raise CloudflareSolveError(f"could not clear Cloudflare in {self.launch_attempts} launches")

    async def __aexit__(self, *exc) -> None:
        await self._teardown()

    async def _has_clearance(self) -> bool:
        return any(c["name"] == "cf_clearance" for c in await self.page.context.cookies())

    async def _click_turnstile(self) -> None:
        try:
            el = await self.page.query_selector('iframe[src*="challenges.cloudflare.com"]')
            if el:
                box = await el.bounding_box()
                if box:
                    await self.page.mouse.click(box["x"] + 30, box["y"] + box["height"] / 2)
        except Exception:  # noqa: BLE001
            pass

    async def _solve_cloudflare(self, *, timeout_s: int = 75) -> None:
        await self.page.goto(self.WARM_URL, wait_until="domcontentloaded", timeout=60000)
        for i in range(timeout_s):
            if await self._has_clearance():
                await self.page.wait_for_timeout(3000)
                return
            if i in (6, 14, 24, 36, 52):
                await self._click_turnstile()
            await self.page.wait_for_timeout(1000)
        raise CloudflareSolveError("could not clear Cloudflare challenge")

    async def get_json(self, path: str, params: dict | None = None) -> Any:
        url = path if path.startswith("http") else f"{self.api_base}{path}"
        for attempt in (1, 2):
            resp = await self.page.context.request.get(
                url, params=params or {}, headers={"Referer": "https://kaito.ai/"})
            if resp.status == 403 and attempt == 1:
                await self._solve_cloudflare()
                continue
            body = await resp.text()
            if resp.status != 200:
                raise RuntimeError(f"kaito {resp.status} for {url}: {body[:160]}")
            return json.loads(body)
        raise RuntimeError(f"kaito: exhausted retries for {url}")
