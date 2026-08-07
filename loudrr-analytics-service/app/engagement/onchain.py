"""Onchain enrichment: resolve call token references + cache market data (FREE APIs).

DexScreener is keyless and free (~300 req/min allowed; we self-cap at 60/min). Pattern:
snapshot at call-extraction time, refresh only ACTIVELY-CALLED tokens, and serve users
exclusively from the eng_token cache — user traffic never touches external APIs.

Resolution rules (spec'd in tests/engagement/test_calls_resolve.py):
  * contract call  -> direct token lookup (the poster gave the address; unambiguous),
  * ticker call    -> search, EXACT symbol match (case-insensitive), top liquidity wins,
                      minimum liquidity floor (scam-copy defense),
  * price_at_call snapshotted ONLY while the call is fresh (< PRICE_SNAPSHOT_WINDOW_H old) —
    backfilled historical calls stay NULL rather than lying (GeckoTerminal OHLCV can fill
    them later),
  * a dead API degrades gracefully — calls stay unresolved, nothing crashes.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta, timezone

import httpx
from aiolimiter import AsyncLimiter
from sqlalchemy import select

from app.db.session import Base, SessionLocal, engine
from app.engagement.models import EngCall, EngToken

logger = logging.getLogger("eng.onchain")

DEX_BASE = "https://api.dexscreener.com"
GECKO_BASE = "https://api.geckoterminal.com/api/v2"
MIN_LIQUIDITY_USD = 10_000.0     # ticker resolution floor (scam-copy defense)
PRICE_SNAPSHOT_WINDOW_H = 24     # only fresh calls get price_at_call
TOKEN_STALE_MINUTES = 30         # refresh cadence for active tokens
ACTIVE_WINDOW_DAYS = 7           # a token is "active" if called within this window

# DexScreener chainId -> GeckoTerminal network id (only where they differ)
GECKO_NETWORKS = {"ethereum": "eth", "polygon": "polygon_pos", "arbitrum": "arbitrum"}

# Native/major coins whose DEX "pairs" are wrapped or counterfeit copies — a $SOL cashtag
# must NEVER resolve to a random pool claiming to be SOL (a fake showed MC $2B on the board).
# Their calls stay recorded (honest) but never reach the leaderboard via DEX resolution.
NATIVE_TICKERS = {
    "BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "DOGE", "TRX", "TON", "AVAX", "DOT",
    "LTC", "BCH", "XMR", "XLM", "SUI", "APT", "NEAR", "ICP", "ATOM", "FIL", "HBAR",
    "USDT", "USDC", "DAI", "ZEC",
}

# Equities/ETFs crypto-twitter cashtags constantly ($MSTR, $NVDA, $TSLA...). DexScreener
# "resolves" them to copycat memecoins (live: MSTR @ $450k mcap, TSLA @ $14k, QQQ on
# Solana) — semantic garbage on a KOL board. Same policy as NATIVE_TICKERS: the calls
# stay recorded, the ticker just never resolves via DEX search.
STOCK_TICKERS = {
    # crypto-adjacent equities
    "MSTR", "COIN", "HOOD", "MARA", "RIOT", "CLSK", "HUT", "CORZ", "WULF", "IREN",
    "CIFR", "BTBT", "HIVE", "BITF", "SBET", "BMNR", "DJT", "CRCL", "GLXY", "BKKT",
    # Strategy tickers + SpaceX-style private-co cashtags
    "STRC", "STRK", "STRF", "STRD", "MSTY", "SPCX",
    # mega-caps + meme stocks
    "NVDA", "TSLA", "META", "AAPL", "MSFT", "AMZN", "GOOG", "GOOGL", "NFLX", "AVGO",
    "ORCL", "BABA", "AMD", "INTC", "MU", "SMCI", "PLTR", "GME", "AMC", "UBER",
    "ABNB", "SHOP", "PYPL", "SOFI", "NIO", "LCID", "RIVN", "SNAP", "RDDT", "OPEN",
    # indices / ETFs / macro tickers
    "SPY", "QQQ", "SPX", "NDX", "DIA", "IWM", "VOO", "VTI", "TQQQ", "SQQQ",
    "VIX", "UVXY", "GLD", "SLV", "USO", "IBIT", "ETHA", "FBTC", "TLT", "DXY",
    # tokenized equities that leaked onto the board via the CONTRACT path (Backpack
    # Securities / Robinhood Chain tokenize real stocks, so they resolve to real pools —
    # semantically still not a crypto KOL call). Seen live 2026-07-16.
    "TSM", "UNH", "GE", "LRCX", "EWY", "CMCSA", "CRWD", "LLY", "BAC", "BA",
    "FTNT", "SMH", "SNOW", "ORCL2", "ASTS", "IRDM", "OUST", "AAOI", "HBM", "LITE",
}

# The CONTRACT-path twin of NATIVE_TICKERS: posting wSOL/WETH/stable addresses directly
# bypassed the ticker guard and put "Wrapped SOL" on the calls board. Same policy —
# the call rows stay recorded (honest), the reference just never resolves. Lowercased;
# compare with .lower() (DexScreener returns checksummed EVM addresses).
WRAPPED_NATIVE_CONTRACTS = {
    "so11111111111111111111111111111111111111112",   # wSOL
    "epjfwdd5aufqssqem2qn1xzybapc8g4weggkzwytdt1v",   # USDC (sol)
    "es9vmfrzacermjfrf4h2fyd4kconky11mcce8benwnyb",   # USDT (sol)
    "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2",     # WETH
    "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599",     # WBTC
    "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c",     # WBNB
    "0x0d500b1d8e8ef31e21c99d1db9a6444d3adf1270",     # WMATIC
    "0xb31f66aa3c1e785363f0875a1b74e27b85fd66c7",     # WAVAX
    "0xdac17f958d2ee523a2206206994597c13d831ec7",     # USDT
    "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",     # USDC
    "0x6b175474e89094c44da98b954eedeac495271d0f",     # DAI
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _proxy_urls() -> list[str]:
    """Webshare proxies as ``http://user:pass@host:port``, the ONE pool every onchain reader
    and the wallet watcher share.

    Source order: env ``ONCHAIN_PROXIES`` (prod/Coolify) first, else the on-disk Webshare file
    (``WEBSHARE_FILE`` or data/proxies/webshare.txt) for local/dev — so candles, trades and the
    RPC watcher all rotate exits instead of hammering one IP into a 429. Empty => direct."""
    def _parse(text: str) -> list[str]:
        urls: list[str] = []
        for ln in re.split(r"[\n,;]+", text):
            ln = ln.strip()
            if not ln or ln.startswith("#"):
                continue
            if ln.startswith("http"):
                urls.append(ln)
            else:
                p = ln.split(":")
                if len(p) == 4:
                    h, port, u, pw = p
                    urls.append(f"http://{u}:{pw}@{h}:{port}")
        return urls

    env = _parse(os.environ.get("ONCHAIN_PROXIES", ""))
    if env:
        return env
    path = os.environ.get("WEBSHARE_FILE", "data/proxies/webshare.txt")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return _parse(f.read())
    return []


class _ProxiedReader:
    """Keyless HTTP reader with optional Webshare proxy rotation.

    DexScreener / GeckoTerminal serve fine from residential IPs but Cloudflare-block our
    Coolify *datacenter* IP (every request fails -> callers degrade to 0 resolved). Routing
    through the rotating Webshare pool (env ``ONCHAIN_PROXIES``) restores access and keeps the
    traffic anonymous — no request ever carries our name. No proxies set -> direct (local dev
    + tests unchanged). Each request is retried across up to ``_MAX_TRIES`` distinct proxies,
    so a single dead/blocked exit IP never fails the call.

    One AsyncClient per exit (proxy or direct), lazily created and pooled — a fresh client per
    request would pay a TCP+TLS handshake every call.
    """

    _MAX_TRIES = 4

    def __init__(self, base_url: str, timeout: float, rate_per_min: int,
                 proxies: list[str] | None = None):
        self._base = base_url
        self._timeout = timeout
        self._proxies = proxies if proxies is not None else _proxy_urls()
        # proxies fan the load across many IPs, so we can safely lift the single-IP self-cap
        if self._proxies:
            rate_per_min = max(rate_per_min, 240)
        self._limiter = AsyncLimiter(rate_per_min, 60)
        self._clients: dict[str | None, httpx.AsyncClient] = {}
        self._rr = 0

    def _client_for(self, proxy: str | None) -> httpx.AsyncClient:
        c = self._clients.get(proxy)
        if c is None or c.is_closed:
            c = (httpx.AsyncClient(base_url=self._base, timeout=self._timeout, proxy=proxy)
                 if proxy else
                 httpx.AsyncClient(base_url=self._base, timeout=self._timeout))
            self._clients[proxy] = c
        return c

    async def _get_json(self, path: str, params: dict | None = None) -> dict:
        await self._limiter.acquire()
        if not self._proxies:
            r = await self._client_for(None).get(path, params=params)
            r.raise_for_status()
            return r.json()
        last: Exception | None = None
        for _ in range(min(self._MAX_TRIES, len(self._proxies))):
            proxy = self._proxies[self._rr % len(self._proxies)]
            self._rr += 1
            try:
                r = await self._client_for(proxy).get(path, params=params)
                r.raise_for_status()
                return r.json()
            except Exception as e:  # noqa: BLE001 — rotate to the next exit IP
                last = e
        raise last if last is not None else RuntimeError("no proxies available")

    async def aclose(self) -> None:
        for c in self._clients.values():
            if not c.is_closed:
                await c.aclose()
        self._clients.clear()


class DexScreenerClient(_ProxiedReader):
    """Minimal keyless DexScreener reader, self-rate-limited well under their free cap."""

    def __init__(self, timeout: float = 20.0, rate_per_min: int = 60,
                 proxies: list[str] | None = None):
        super().__init__(DEX_BASE, timeout, rate_per_min, proxies)

    async def token_pairs(self, contract: str) -> list[dict]:
        d = await self._get_json(f"/latest/dex/tokens/{contract}")
        return d.get("pairs") or []

    async def search(self, query: str) -> list[dict]:
        d = await self._get_json("/latest/dex/search", params={"q": query})
        return d.get("pairs") or []


class GeckoTerminalClient(_ProxiedReader):
    """Keyless GeckoTerminal reader (30 req/min free cap; we self-cap at 20)."""

    def __init__(self, timeout: float = 20.0, rate_per_min: int = 20,
                 proxies: list[str] | None = None):
        super().__init__(GECKO_BASE, timeout, rate_per_min, proxies)

    async def ohlcv(self, network: str, pool: str, *, timeframe: str = "hour",
                    aggregate: int = 4, limit: int = 200) -> list[list[float]]:
        """[[unix_ts, o, h, l, c(, v)] ...] for the pool, oldest-first from the API."""
        d = await self._get_json(
            f"/networks/{network}/pools/{pool}/ohlcv/{timeframe}",
            params={"aggregate": aggregate, "limit": limit})
        return (((d.get("data") or {}).get("attributes") or {}).get("ohlcv_list") or [])

    async def trades(self, network: str, pool: str) -> list[dict]:
        """Recent pool trades (attributes dicts incl. tx_from_address / kind / prices) —
        the keyless feed KOL Rides intersects with the wallet vault."""
        d = await self._get_json(f"/networks/{network}/pools/{pool}/trades")
        return [(x.get("attributes") or {}) for x in (d.get("data") or [])]


def _liq(p: dict) -> float:
    try:
        return float((p.get("liquidity") or {}).get("usd") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _pick_pair(pairs: list[dict], *, symbol: str | None = None, floor: bool = False) -> dict | None:
    """Best pair for a reference.

    Ticker path (symbol given): FIRST exact-symbol result above the liquidity floor, in the
    provider's OWN order — scam pools SPOOF liquidity, so "highest liquidity wins" is exactly
    the rule attackers game (live: LINK/UNI/AAVE resolved to counterfeits with fake $1B+
    pools). DexScreener's ranking is the better trust signal.
    Contract path (no symbol): the poster gave the address — just take the deepest pool of
    that token for metadata.
    """
    cand = []
    for p in pairs or []:
        base = p.get("baseToken") or {}
        if symbol and str(base.get("symbol") or "").upper() != symbol.upper():
            continue
        if floor and _liq(p) < MIN_LIQUIDITY_USD:
            continue
        if base.get("address"):
            if symbol:
                return p  # provider order wins on the spoofable ticker path
            cand.append(p)
    return max(cand, key=_liq) if cand else None


MAX_MCAP_USD = 1e13            # > $10T is spoofed (BTC ~ $2T)
MAX_MCAP_LIQUIDITY_RATIO = 1e5  # real pairs run ~1-10^4; scam pools spoof mcap vs tiny liq
MAX_ABS_CHANGE_PCT = 1e5        # a 1000x day is +100,000%; beyond that it's corrupt data
MAX_PRICE_USD = 1e9             # no real token trades at > $1B/unit; spoofed price feed


def _token_from_pair(pair: dict) -> dict | None:
    """Column-safe token fields from a pair, or None if the token can't be stored.

    Every STRING crossing the external-API boundary is clamped to its column width — a
    single 66-char pool_address (32-byte hash) used to roll back whole enrichment batches
    on Postgres (StringDataRightTruncationError at autoflush). Identifiers (contract,
    pool_address, image URL) are nulled/skip rather than truncated: a cut identifier is
    corrupt, not shorter. Labels (symbol, name) truncate harmlessly.
    """
    base = pair.get("baseToken") or {}

    def num(v):
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    # Sanity clamps — scam pools spoof marketCap/priceUsd/priceChange (seen live: +2.7e39%,
    # $1.9T "mcap"); absurd numbers become NULL rather than reaching the UI. Every numeric
    # field crossing the external-API boundary gets validated here.
    liq = num((pair.get("liquidity") or {}).get("usd"))  # None = absent; 0.0 = drained pool
    mcap = num(pair.get("marketCap") or pair.get("fdv"))
    if mcap is not None and (
        mcap > MAX_MCAP_USD
        or liq == 0.0                                   # drained pool -> mcap is stale garbage
        or (liq and mcap / liq > MAX_MCAP_LIQUIDITY_RATIO)
    ):
        mcap = None
    change = num((pair.get("priceChange") or {}).get("h24"))
    if change is not None and abs(change) > MAX_ABS_CHANGE_PCT:
        change = None
    price = num(pair.get("priceUsd"))
    if price is not None and (price > MAX_PRICE_USD or price < 0):
        price = None

    def label(v, width):  # display text: truncation is harmless
        return str(v)[:width] if v else None

    def ident(v, width):  # identifier: truncated = corrupt -> null instead
        s = str(v) if v else None
        return s if s and len(s) <= width else None

    contract = ident(base.get("address"), 128)
    if not contract:  # un-storable primary key -> skip this token, never the batch
        logger.warning("token skipped, contract too long: %r", str(base.get("address"))[:160])
        return None
    chain_id = pair.get("chainId")
    return {
        "contract": contract,
        "chain": label(chain_id, 16),
        "symbol": label(base.get("symbol"), 32),
        "name": label(base.get("name"), 128),
        "image": ident((pair.get("info") or {}).get("imageUrl"), 255),
        "price_usd": price,
        "mcap_usd": mcap,
        "change_24h": change,
        "liquidity_usd": liq,
        # pool + GeckoTerminal network id — enables free OHLCV for the call chart
        "pool_address": ident(pair.get("pairAddress"), 128),
        "network": label(GECKO_NETWORKS.get(str(chain_id or ""), chain_id), 16),
        "refreshed_at": _utcnow(),
    }


async def _upsert_token(s, fields: dict | None) -> EngToken | None:
    if fields is None:  # _token_from_pair skipped an un-storable token
        return None
    tok = await s.get(EngToken, fields["contract"])
    if tok is None:
        tok = EngToken(**fields)
        s.add(tok)
    else:
        for k, v in fields.items():
            setattr(tok, k, v)
    return tok


async def enrich_calls(*, dex=None, limit: int = 200, max_id: int | None = None) -> dict:
    """Resolve unresolved calls -> eng_token rows + token_contract / price snapshots.

    Newest-first. Permanently-unresolvable calls (native cashtags, stock tickers, garbage
    base58) stay NULL forever, so the newest-N window eventually fills with duds and a naive
    re-poll stalls there — never reaching older resolvable calls. ``max_id`` is the keyset
    cursor for a full backlog sweep: pass the previous batch's ``min_id`` to page strictly
    downward so every unresolved call is examined exactly once per run (see enrich_prod).
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    dex = dex or DexScreenerClient()

    resolved = 0
    async with SessionLocal() as s:
        q = select(EngCall).where(EngCall.token_contract.is_(None))
        if max_id is not None:
            q = q.where(EngCall.id < max_id)
        calls = (await s.execute(
            q.order_by(EngCall.id.desc()).limit(limit))).scalars().all()
        if not calls:
            return {"resolved": 0, "examined": 0, "min_id": None}

        # resolve each distinct reference once (many calls share a token)
        by_contract = {c.contract for c in calls if c.contract}
        by_ticker = {c.ticker for c in calls if not c.contract and c.ticker}
        tokens: dict[str, EngToken] = {}   # reference (contract or $TICKER) -> token

        for contract in by_contract:
            # wrapped-native/stable addresses: the contract-path twin of the ticker guard
            if contract.lower() in WRAPPED_NATIVE_CONTRACTS:
                continue
            try:
                pick = _pick_pair(await dex.token_pairs(contract))
            except Exception as e:  # noqa: BLE001 — free API down = degrade, don't die
                logger.warning("dex token_pairs %s: %s", contract, e)
                continue
            if pick:
                # SYMBOL guard on the CONTRACT path — the ticker path blocks $HOOD/$TSM, but
                # posting the tokenized-equity CONTRACT bypassed it entirely and put stocks
                # (TSM/UNH/GE/HOOD…) on a crypto KOL board. Resolve first, then apply the same
                # policy to the RESOLVED symbol: the call stays recorded, it just never ranks.
                fields = _token_from_pair(pick)
                sym = ((fields or {}).get("symbol") or "").upper()
                if sym and (sym in STOCK_TICKERS or sym in NATIVE_TICKERS):
                    continue
                tokens[contract] = await _upsert_token(s, fields)

        for ticker in by_ticker:
            # native coins (DEX pairs are wrapped/fake) + equities (DEX "matches" are
            # copycat memecoins): never resolve — the honest answer is no answer
            if ticker.upper() in NATIVE_TICKERS or ticker.upper() in STOCK_TICKERS:
                continue
            # STICKY resolution: a ticker that already resolved keeps its contract forever —
            # re-searching can flap to a different pool and split one token into duplicates
            canon = (await s.execute(
                select(EngCall.token_contract)
                .where(EngCall.ticker == ticker, EngCall.token_contract.is_not(None))
                .order_by(EngCall.captured_at.desc()).limit(1)
            )).scalars().first()
            if canon:
                tok = await s.get(EngToken, canon)
                if tok is None:
                    # canon holds even without metadata: hydrate via the CANON contract
                    # (direct lookup), never via search — search is what flaps
                    try:
                        pick = _pick_pair(await dex.token_pairs(canon))
                        if pick:
                            tok = await _upsert_token(s, _token_from_pair(pick))
                    except Exception as e:  # noqa: BLE001
                        logger.warning("dex canon hydrate %s: %s", canon, e)
                tokens[f"${ticker}"] = tok if tok is not None else EngToken(contract=canon)
                continue
            try:
                pick = _pick_pair(await dex.search(ticker), symbol=ticker, floor=True)
            except Exception as e:  # noqa: BLE001
                logger.warning("dex search %s: %s", ticker, e)
                continue
            if pick:
                tokens[f"${ticker}"] = await _upsert_token(s, _token_from_pair(pick))

        fresh_cutoff = _utcnow() - timedelta(hours=PRICE_SNAPSHOT_WINDOW_H)
        for c in calls:
            tok = tokens.get(c.contract or "") or tokens.get(f"${c.ticker}" if c.ticker else "")
            if tok is None:
                continue
            c.token_contract = tok.contract
            c.chain = tok.chain or c.chain
            if c.ts is not None and c.ts >= fresh_cutoff and c.price_at_call is None:
                c.price_at_call = tok.price_usd
                c.mcap_at_call = tok.mcap_usd
            resolved += 1
        await s.commit()

    return {"resolved": resolved, "examined": len(calls),
            "min_id": min(c.id for c in calls)}


async def capture_rides(contract: str, network: str, pool: str, *, gecko=None) -> dict:
    """Intersect a token pool's recent trades with the wallet vault -> persist KOL Rides.

    Idempotent on tx_hash, so every capture (chart views + worker refreshes) accumulates
    history beyond the feed's short window. Only identity-mapped wallets become rides —
    an anonymous vault address can't be pinned to a KOL. Never raises.
    """
    from app.engagement.models import EngRide, EngWallet  # local import avoids cycles

    gecko = gecko or GeckoTerminalClient()
    try:
        trades = await gecko.trades(network, pool)
    except Exception as e:  # noqa: BLE001 — free feed down = no capture this round
        logger.warning("trades %s/%s: %s", network, pool, e)
        return {"rides_inserted": 0, "trades_seen": 0}

    def num(v):
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    inserted = 0
    async with SessionLocal() as s:
        vault = {w.address: w for w in (await s.execute(
            select(EngWallet).where(EngWallet.handle.is_not(None)))).scalars().all()}
        if vault:
            hits = [t for t in trades if str(t.get("tx_from_address") or "") in vault]
            if hits:
                existing = set((await s.execute(
                    select(EngRide.tx_hash).where(
                        EngRide.tx_hash.in_([str(t.get("tx_hash")) for t in hits]))
                )).scalars().all())
                for t in hits:
                    tx = str(t.get("tx_hash") or "")
                    if not tx or tx in existing:
                        continue
                    existing.add(tx)
                    w = vault[str(t.get("tx_from_address"))]
                    ts = None
                    raw_ts = t.get("block_timestamp")
                    if raw_ts:
                        try:
                            ts = datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00")) \
                                .astimezone(timezone.utc).replace(tzinfo=None)
                        except ValueError:
                            ts = None
                    side = "buy" if str(t.get("kind") or "").lower() == "buy" else "sell"
                    s.add(EngRide(
                        token_contract=contract, wallet_address=w.address,
                        member_id=w.member_id, handle=w.handle, side=side,
                        price_usd=num(t.get("price_to_in_usd")) or num(t.get("price_from_in_usd")),
                        volume_usd=num(t.get("volume_in_usd")),
                        ts=ts, tx_hash=tx,
                    ))
                    inserted += 1
        await s.commit()
    return {"rides_inserted": inserted, "trades_seen": len(trades)}


async def refresh_tokens(*, dex=None) -> dict:
    """Refresh market data for ACTIVE tokens (recent calls) whose cache is stale."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    dex = dex or DexScreenerClient()

    refreshed = 0
    async with SessionLocal() as s:
        active_cutoff = (_utcnow() - timedelta(days=ACTIVE_WINDOW_DAYS)).date()
        active = set((await s.execute(
            select(EngCall.token_contract).distinct()
            .where(EngCall.token_contract.is_not(None), EngCall.day >= active_cutoff)
        )).scalars().all())
        if not active:
            return {"refreshed": 0}

        stale_cutoff = _utcnow() - timedelta(minutes=TOKEN_STALE_MINUTES)
        stale = (await s.execute(
            select(EngToken).where(
                EngToken.contract.in_(active),
                (EngToken.refreshed_at.is_(None)) | (EngToken.refreshed_at < stale_cutoff))
        )).scalars().all()

        for tok in stale:
            try:
                pick = _pick_pair(await dex.token_pairs(tok.contract))
            except Exception as e:  # noqa: BLE001
                logger.warning("dex refresh %s: %s", tok.contract, e)
                continue
            if pick and await _upsert_token(s, _token_from_pair(pick)) is not None:
                refreshed += 1
        await s.commit()

    return {"refreshed": refreshed}
