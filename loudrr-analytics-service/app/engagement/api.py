"""Public smart-engagement + KOL-calls API.

GET /v1/smart-engagement?userName=X ->
    { userName, userId, counts: {"YYYY-MM-DD": n, ...}, total, firstData, updated, coverage }
GET /v1/kol-calls?window=24h|7d|30d ->
    { window, items: [{contract, chain, symbol, name, image, priceUsd, mcapUsd, change24h,
                       calls, kols, kolSample: [{username, count}]}], updated, coverage }
GET /v1/kol-calls/token?contract=X ->
    { token, calls: [{username, tweetId, ts, priceAtCall, confidence}] }

Design contract (locked): serving reads LOCAL tables only (never a gateway or external API in
the request path), and handlers can NEVER 500 — any failure degrades to an empty 200 so the
public score funnel is untouchable by this feature.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.db.models import RankedAccount, SmartSetMember
from app.db.session import SessionLocal
from app.engagement.models import EngCall, EngEdge, EngRide, EngToken, EngWallet

logger = logging.getLogger("eng.api")

router = APIRouter()

WINDOW_DAYS = 364  # 52 weeks — matches the heatmap grid
CALL_WINDOWS = {"24h": timedelta(hours=24), "7d": timedelta(days=7), "30d": timedelta(days=30)}

# Chart candles: the ONE deliberate exception to "serving reads local tables only" — a free
# keyless GeckoTerminal read, TTL-cached in-process and fully degrading (chart absent, page
# fine). Never the paid gateway. `_gecko` is module-level so tests can inject a fake.
from app.engagement.onchain import (  # noqa: E402
    NATIVE_TICKERS, STOCK_TICKERS, WRAPPED_NATIVE_CONTRACTS, GeckoTerminalClient,
)

_gecko = GeckoTerminalClient()
_chart_cache: dict[str, tuple[float, list[dict]]] = {}
_CHART_TTL_S = 300.0

# Chart timeframe chips (Bitget/morfi-style) -> GeckoTerminal (timeframe, aggregate, limit).
# limit chosen so each frame spans a sensible window: 5m×288≈1d, 1h×168≈7d, 1D×180≈6mo.
CHART_TFS: dict[str, tuple[str, int, int]] = {
    "5m": ("minute", 5, 288),
    "15m": ("minute", 15, 192),
    "1h": ("hour", 1, 168),
    "4h": ("hour", 4, 180),
    "1d": ("day", 1, 180),
}
_TAPE_TTL_S = 20.0
_tape_cache: dict[str, tuple[float, list[dict]]] = {}


def _to_candles(raw: list) -> list[dict]:
    """GeckoTerminal ohlcv_list rows -> real OHLC candles, oldest-first.

    Rows are [unix_ts, open, high, low, close, volume]. We used to keep row[4] (close) alone
    and draw a line — the open/high/low/volume were fetched and thrown away on every call.
    Emitting them costs nothing and is what makes an exchange-grade candle chart possible.
    `price` (== close) stays so existing line-chart callers keep working unchanged.
    """
    out: list[dict] = []
    for row in raw or []:
        if len(row) < 5 or row[4] is None:
            continue
        try:
            ts = int(row[0])
            o, h, low, c = (float(row[1]), float(row[2]), float(row[3]), float(row[4]))
        except (TypeError, ValueError):
            continue
        # a zero/negative OHLC is corrupt feed data, not a real bar — it would blow up the
        # chart's log scale and drag the whole price domain to zero
        if min(o, h, low, c) <= 0:
            continue
        vol = 0.0
        if len(row) >= 6 and row[5] is not None:
            try:
                vol = float(row[5])
            except (TypeError, ValueError):
                vol = 0.0
        out.append({"ts": ts, "open": o, "high": h, "low": low, "close": c,
                    "volume": vol, "price": c})
    # Strictly-increasing, de-duplicated bars. A real charting library (unlike the old
    # hand-rolled line) THROWS on duplicate or unordered timestamps, and the feed does
    # repeat the newest bar across overlapping pulls — so dedupe here, once, rather than
    # in every caller. Later row wins: it's the fresher read of that same bar.
    dedup: dict[int, dict] = {}
    for bar in out:
        dedup[bar["ts"]] = bar
    return [dedup[k] for k in sorted(dedup)]

# ── panel coverage curve (global, cached) ────────────────────────────────────
# Timelines are fetched newest-first with a page cap, so each member's window is a
# CONTIGUOUS [oldest_fetched, now] — meaning a year-ago day is only "seen" by the slice
# of the panel whose window reaches back that far (~22% at -12mo vs ~100% recent). Raw
# counts therefore show a fake "everyone grew 30x" gradient. The API exposes the real
# per-day coverage fraction so the UI can mark partial days instead of implying zeros.
_coverage_cache: tuple[float, dict[str, float], str | None, int] | None = None
_COVERAGE_TTL_S = 6 * 3600.0


async def _panel_coverage() -> tuple[dict[str, float], str | None, int]:
    """({day-iso: fraction 0..1}, tracked_since_iso, panel_size) for the heatmap window.

    fraction = members whose fetched window includes that day / panel size;
    tracked_since = earliest day with >= 90% coverage (the "fully tracked" boundary).
    Cached: one GROUP BY over eng_tweet_raw per 6h, never per-request.
    """
    global _coverage_cache
    import time as _time
    now = _time.monotonic()
    if _coverage_cache is not None and now - _coverage_cache[0] < _COVERAGE_TTL_S:
        return _coverage_cache[1], _coverage_cache[2], _coverage_cache[3]

    from app.engagement.models import EngTweetRaw
    async with SessionLocal() as s:
        oldest_rows = (await s.execute(
            select(func.min(EngTweetRaw.created_at))
            .where(EngTweetRaw.created_at.is_not(None))
            .group_by(EngTweetRaw.member_id)
        )).scalars().all()
    panel = len(oldest_rows)
    days = [date.today() - timedelta(days=i) for i in range(WINDOW_DAYS - 1, -1, -1)]
    curve: dict[str, float] = {}
    tracked_since: str | None = None
    if panel:
        starts = sorted(d.date() for d in oldest_rows if d is not None)
        import bisect
        for d in days:
            frac = bisect.bisect_right(starts, d) / panel
            curve[d.isoformat()] = round(frac, 3)
            if tracked_since is None and frac >= 0.9:
                tracked_since = d.isoformat()
    _coverage_cache = (now, curve, tracked_since, panel)
    return curve, tracked_since, panel


@router.get("/smart-engagement")
async def smart_engagement(
    user_name: str | None = Query(None, alias="userName"),
    username: str | None = Query(None, alias="username"),
    user_id: str | None = Query(None, alias="user_id"),
):
    uname = (user_name or username or "").strip().lstrip("@").lower()
    empty = {
        "userName": uname or None, "userId": user_id, "counts": {}, "total": 0,
        "firstData": None, "updated": datetime.now(timezone.utc).isoformat(),
        "coverage": "none", "panelCoverage": {}, "trackedSince": None, "panelSize": 0,
    }
    try:
        uid = (user_id or "").strip()
        if not uname and not uid:
            return empty
        since = date.today() - timedelta(days=WINDOW_DAYS)

        async with SessionLocal() as s:
            if uname and not uid:
                # local resolution only (handle-change safety); unknown handles stay id-less
                uid = (await s.execute(
                    select(SmartSetMember.user_id)
                    .where(func.lower(SmartSetMember.username) == uname).limit(1)
                )).scalars().first() or ""

            # Identity rule: once resolved to an id, match by id ONLY — edges freeze
            # target_username at write time, so the username arm would blend a recycled
            # handle's PREVIOUS owner's history into the new owner's heatmap. The
            # username arm exists solely for handles we can't resolve locally.
            if uid:
                cond = EngEdge.target_id == str(uid)
            else:
                cond = EngEdge.target_username == uname
            rows = (await s.execute(
                select(EngEdge.day, func.count(func.distinct(EngEdge.engager_id)))
                .where(cond, EngEdge.day >= since)
                .group_by(EngEdge.day).order_by(EngEdge.day)
            )).all()

        counts = {d.isoformat(): int(n) for d, n in rows}
        try:
            panel_coverage, tracked_since, panel_size = await _panel_coverage()
        except Exception:  # noqa: BLE001 — coverage is an annotation, never a blocker
            logger.exception("panel coverage computation failed")
            panel_coverage, tracked_since, panel_size = {}, None, 0
        return {
            **empty,
            "userId": uid or None,
            "counts": counts,
            "total": sum(counts.values()),
            "firstData": min(counts) if counts else None,
            "coverage": "tracked" if counts else "none",
            # honest-heatmap annotations: per-day fraction of the panel whose fetched
            # window includes that day, the >=90% boundary, and the panel size
            "panelCoverage": panel_coverage,
            "trackedSince": tracked_since,
            "panelSize": panel_size,
        }
    except Exception:  # noqa: BLE001 — this endpoint degrades, never errors
        logger.exception("smart-engagement lookup failed for %r", uname)
        return empty


@router.get("/kol-calls")
async def kol_calls(window: str = Query("24h")):
    """Token leaderboard by KOL calls in the window — the Bitget-style 'KOL signals' feed."""
    window = window if window in CALL_WINDOWS else "24h"
    empty = {"window": window, "items": [],
             "updated": datetime.now(timezone.utc).isoformat(), "coverage": "none"}
    try:
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - CALL_WINDOWS[window]
        # calls are counted by DISTINCT tweet — one tweet carrying both the $TICKER and the
        # contract of the same token produces two eng_call rows but is ONE call
        n_calls = func.count(func.distinct(EngCall.tweet_id))
        async with SessionLocal() as s:
            rows = (await s.execute(
                select(EngCall.token_contract,
                       n_calls,
                       func.count(func.distinct(EngCall.member_id)))
                .where(EngCall.token_contract.is_not(None), EngCall.ts >= cutoff,
                       # wSOL/WETH/stables posted as contracts are quote-currency noise,
                       # not calls — same policy as NATIVE_TICKERS on the ticker path
                       func.lower(EngCall.token_contract).not_in(WRAPPED_NATIVE_CONTRACTS))
                .group_by(EngCall.token_contract)
                .order_by(n_calls.desc())
                .limit(50)
            )).all()
            contracts = [r[0] for r in rows]
            tokens = {t.contract: t for t in (await s.execute(
                select(EngToken).where(EngToken.contract.in_(contracts)))).scalars().all()
            } if contracts else {}
            # per-token KOL sample (usernames for the avatar row), one grouped query
            samples: dict[str, list[dict]] = {}
            if contracts:
                for contract, uname, n in (await s.execute(
                    select(EngCall.token_contract, SmartSetMember.username, n_calls)
                    .join(SmartSetMember, SmartSetMember.user_id == EngCall.member_id)
                    .where(EngCall.token_contract.in_(contracts), EngCall.ts >= cutoff)
                    .group_by(EngCall.token_contract, SmartSetMember.username)
                    .order_by(n_calls.desc())
                )).all():
                    bucket = samples.setdefault(contract, [])
                    if len(bucket) < 6 and uname:
                        bucket.append({"username": uname, "count": int(n)})

        items = []
        for contract, n_calls, n_kols in rows:
            tok = tokens.get(contract)
            if tok is None:
                continue  # unresolved tokens never reach the UI
            # SERVE-TIME guard: rows resolved BEFORE the contract-path symbol fix (tokenized
            # equities like TSM/UNH/GE/HOOD posted as contracts by Backpack/Robinhood) are
            # already in the DB — filter them here so the board is clean without a re-crawl.
            sym = (tok.symbol or "").upper()
            if sym in STOCK_TICKERS or sym in NATIVE_TICKERS:
                continue
            items.append({
                "contract": contract, "chain": tok.chain, "symbol": tok.symbol,
                "name": tok.name, "image": tok.image,
                "priceUsd": tok.price_usd, "mcapUsd": tok.mcap_usd,
                "change24h": tok.change_24h, "liquidityUsd": tok.liquidity_usd,
                "calls": int(n_calls), "kols": int(n_kols),
                "kolSample": samples.get(contract, []),
            })
        return {**empty, "items": items, "coverage": "tracked" if items else "none"}
    except Exception:  # noqa: BLE001 — degrades, never errors
        logger.exception("kol-calls leaderboard failed")
        return empty


@router.get("/kol-calls/token")
async def kol_calls_token(contract: str = Query("")):
    """Every tracked KOL call for one token, newest first."""
    empty = {"token": None, "calls": [],
             "updated": datetime.now(timezone.utc).isoformat()}
    try:
        contract = contract.strip()
        if not contract:
            return empty
        async with SessionLocal() as s:
            tok = await s.get(EngToken, contract)
            if tok is None:
                return empty
            rows = (await s.execute(
                select(EngCall, SmartSetMember.username)
                .join(SmartSetMember, SmartSetMember.user_id == EngCall.member_id, isouter=True)
                .where(EngCall.token_contract == contract)
                .order_by(EngCall.ts.desc()).limit(200)
            )).all()
        # one entry per TWEET (a ticker+contract tweet yields two rows for the same call);
        # rows are newest-first so the first occurrence wins
        seen_tweets: set[str] = set()
        calls = []
        for c, uname in rows:
            if c.tweet_id in seen_tweets:
                continue
            seen_tweets.add(c.tweet_id)
            calls.append({
                "username": uname, "tweetId": c.tweet_id,
                "ts": c.ts.isoformat() if c.ts else None,
                "priceAtCall": c.price_at_call, "confidence": c.confidence,
            })
            if len(calls) >= 100:
                break
        return {
            **empty,
            "token": {
                "contract": tok.contract, "chain": tok.chain, "symbol": tok.symbol,
                "name": tok.name, "image": tok.image, "priceUsd": tok.price_usd,
                "mcapUsd": tok.mcap_usd, "change24h": tok.change_24h,
            },
            "calls": calls,
        }
    except Exception:  # noqa: BLE001 — degrades, never errors
        logger.exception("kol-calls token detail failed for %r", contract)
        return empty


@router.get("/kol-calls/chart")
async def kol_calls_chart(contract: str = Query(""), timeframe: str = Query("4h")):
    """Candles + KOL call points for one token — the Bitget-style call chart.

    timeframe: 5m|15m|1h|4h|1d (the chart chips) -> GeckoTerminal OHLCV granularity.
    candles: [{ts, open, high, low, close, volume, price}] (price == close, back-compat);
    calls: [{username, ts, priceAtCall}];
    rides: [{username, side, ts, price, volumeUsd}] — tracked KOL wallets trading this token.
    Live updates to the newest candle arrive on the WS at /v1/kol-calls/live.
    """
    timeframe = timeframe if timeframe in CHART_TFS else "4h"
    gtf, agg, limit = CHART_TFS[timeframe]
    empty = {"token": None, "candles": [], "calls": [], "rides": [], "timeframe": timeframe,
             "updated": datetime.now(timezone.utc).isoformat()}
    try:
        contract = contract.strip()
        if not contract:
            return empty
        async with SessionLocal() as s:
            tok = await s.get(EngToken, contract)
            if tok is None:
                return empty
            cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=32)
            rows = (await s.execute(
                select(EngCall, SmartSetMember.username)
                .join(SmartSetMember, SmartSetMember.user_id == EngCall.member_id, isouter=True)
                .where(EngCall.token_contract == contract, EngCall.ts >= cutoff)
                .order_by(EngCall.ts.asc()).limit(400)
            )).all()

        seen: set[str] = set()
        calls = []
        for c, uname in rows:
            if c.tweet_id in seen:
                continue
            seen.add(c.tweet_id)
            calls.append({"username": uname, "tweetId": c.tweet_id,
                          "ts": c.ts.isoformat() if c.ts else None,
                          "priceAtCall": c.price_at_call})

        candles: list[dict] = []
        if tok.pool_address and tok.network:
            import time
            ckey = f"{contract}:{timeframe}"          # cache per (token, timeframe)
            hit = _chart_cache.get(ckey)
            if hit and time.monotonic() - hit[0] < _CHART_TTL_S:
                candles = hit[1]
            else:
                try:
                    raw = await _gecko.ohlcv(tok.network, tok.pool_address,
                                             timeframe=gtf, aggregate=agg, limit=limit)
                    candles = _to_candles(raw)
                    _chart_cache[ckey] = (time.monotonic(), candles)
                except Exception as e:  # noqa: BLE001 — chart absent, page fine
                    logger.warning("ohlcv %s: %s", contract, e)
                # same cache-miss beat also captures fresh KOL Rides (bounded feed calls)
                try:
                    from app.engagement.onchain import capture_rides
                    await capture_rides(contract, tok.network, tok.pool_address, gecko=_gecko)
                except Exception:  # noqa: BLE001
                    logger.exception("ride capture failed for %r", contract)

        async with SessionLocal() as s:
            # LEFT JOIN the vault so each ride carries its wallet's Loudrr score (scored like
            # any web account — smart-set followers + base floor — NOT an account crawl)
            ride_rows = (await s.execute(
                select(EngRide, EngWallet.score, EngWallet.smart_followers)
                .join(EngWallet, EngWallet.handle == EngRide.handle, isouter=True)
                .where(EngRide.token_contract == contract, EngRide.ts >= cutoff)
                .order_by(EngRide.ts.asc()).limit(200)
            )).all()
        rides = [{
            "username": r.handle, "side": r.side,
            "ts": r.ts.isoformat() if r.ts else None,
            "price": r.price_usd, "volumeUsd": r.volume_usd,
            "score": score, "tier": _tier_for(score) if score is not None else None,
            "smartFollowers": sf,
        } for r, score, sf in ride_rows]

        return {
            **empty,
            "token": {
                "contract": tok.contract, "chain": tok.chain, "symbol": tok.symbol,
                "name": tok.name, "image": tok.image, "priceUsd": tok.price_usd,
                "change24h": tok.change_24h,
            },
            "candles": candles,
            "calls": calls,
            "rides": rides,
        }
    except Exception:  # noqa: BLE001 — degrades, never errors
        logger.exception("kol-calls chart failed for %r", contract)
        return empty


async def tape_snapshot(contract: str, *, fresh: bool = False) -> list[dict]:
    """Recent pool trades, newest-first, KOL-labeled. Shared by the REST tape and the
    realtime hub (app/engagement/live.py) so both read ONE cached feed pull.

    fresh=True bypasses the TTL cache — the hub's poller IS the refresh, and serving it a
    20s-stale snapshot would cap realtime at 20s no matter how often it polls. It still
    writes the cache, so a hub-watched token makes the REST tape free for everyone else.
    Returns [] on any failure; never raises.
    """
    import time
    contract = (contract or "").strip()
    if not contract:
        return []
    hit = _tape_cache.get(contract)
    if not fresh and hit and time.monotonic() - hit[0] < _TAPE_TTL_S:
        return hit[1]

    async with SessionLocal() as s:
        tok = await s.get(EngToken, contract)
        if tok is None or not (tok.pool_address and tok.network):
            return []
        # vault lookup: address -> (handle, loudrr score) for identity-mapped wallets
        vault = {w.address: (w.handle, w.score) for w in (await s.execute(
            select(EngWallet).where(EngWallet.handle.is_not(None)))).scalars().all()}

    try:
        raw = await _gecko.trades(tok.network, tok.pool_address)
    except Exception as e:  # noqa: BLE001 — feed down = empty tape, page fine
        logger.warning("tape trades %s: %s", contract, e)
        return []

    def num(v):
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    trades = []
    for t in raw:
        wallet = str(t.get("tx_from_address") or "")
        if not wallet:
            continue
        handle, score = vault.get(wallet, (None, None))
        kind = str(t.get("kind") or "").lower()
        ts = None
        raw_ts = t.get("block_timestamp")
        if raw_ts:
            try:
                ts = datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00")) \
                    .astimezone(timezone.utc).isoformat()
            except ValueError:
                ts = None
        trades.append({
            "wallet": f"{wallet[:4]}…{wallet[-4:]}" if len(wallet) > 10 else wallet,
            "handle": handle,                       # None for non-KOL buyers
            "isKol": handle is not None,
            "score": score, "tier": _tier_for(score) if score is not None else None,
            "side": "buy" if kind == "buy" else "sell",
            "volumeUsd": num(t.get("volume_in_usd")),
            "priceUsd": num(t.get("price_to_in_usd")) or num(t.get("price_from_in_usd")),
            "ts": ts,
        })
    # newest first; KOL trades already flagged for UI emphasis
    trades.sort(key=lambda x: x["ts"] or "", reverse=True)
    _tape_cache[contract] = (time.monotonic(), trades)
    return trades


async def candle_snapshot(contract: str, timeframe: str = "5m") -> dict | None:
    """The newest (still-forming) candle for a token — what the hub streams so the chart can
    series.update() in place instead of refetching the whole series. None on any failure.
    """
    try:
        gtf, agg, _ = CHART_TFS.get(timeframe, CHART_TFS["5m"])
        async with SessionLocal() as s:
            tok = await s.get(EngToken, contract)
        if tok is None or not (tok.pool_address and tok.network):
            return None
        raw = await _gecko.ohlcv(tok.network, tok.pool_address,
                                 timeframe=gtf, aggregate=agg, limit=2)
        bars = _to_candles(raw)
        return bars[-1] if bars else None
    except Exception:  # noqa: BLE001 — no candle frame, socket + page unaffected
        logger.warning("candle snapshot failed for %r", contract, exc_info=True)
        return None


@router.get("/kol-calls/tape")
async def kol_calls_tape(contract: str = Query(""), limit: int = Query(30)):
    """Live trade tape for a token — the 'X bought $Y just now' ticker under the chart.

    Recent pool trades from GeckoTerminal (keyless), each labeled with the KOL handle when the
    buyer is a wallet in our vault (isKol=true) else an abbreviated address. 20s-cached; the
    realtime path is the WS at /v1/kol-calls/live, this stays for no-WS fallback. Never errors.
    """
    empty = {"contract": contract, "trades": [],
             "updated": datetime.now(timezone.utc).isoformat()}
    try:
        trades = await tape_snapshot(contract)
        return {**empty, "trades": trades[:limit]}
    except Exception:  # noqa: BLE001 — degrades, never errors
        logger.exception("kol-calls tape failed for %r", contract)
        return empty


# Social Buzz windows -> (lookback, bucket width). X-activity buzz is a coarser, longer view
# than the onchain tape, so buckets are hours-to-a-day wide.
BUZZ_WINDOWS = {"7d": (timedelta(days=7), timedelta(hours=6)),
                "30d": (timedelta(days=30), timedelta(days=1))}


@router.get("/kol-calls/buzz")
async def kol_calls_buzz(contract: str = Query(""), window: str = Query("7d")):
    """X-activity 'Social Buzz' for one token — the OFFCHAIN counterpart to KOL Rides.

    buzz = smart-set call activity, weighted by each caller's Loudrr influence (a call from a
    Megaphone counts more than one from a Whisper), bucketed over the window and normalized
    0-100. Plus the per-KOL 'signal' leaderboard for this token (who's talking, ranked by
    score-weighted signal count). Pure eng_call — no onchain, no external calls.
    """
    window = window if window in BUZZ_WINDOWS else "7d"
    lookback, bucket = BUZZ_WINDOWS[window]
    empty = {"contract": contract, "window": window, "buzzIndex": 0.0,
             "series": [], "signals": [], "coverage": "none",
             "updated": datetime.now(timezone.utc).isoformat()}
    try:
        contract = contract.strip()
        if not contract:
            return empty
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        cutoff = now - lookback
        bucket_s = bucket.total_seconds()

        async with SessionLocal() as s:
            # one row per (call, caller) with the caller's PUBLIC Loudrr score + identity
            # (RankedAccount = the same 0-6000 number shown on the leaderboard/profile)
            rows = (await s.execute(
                select(EngCall.tweet_id, EngCall.ts, EngCall.member_id,
                       RankedAccount.username, RankedAccount.score,
                       RankedAccount.followers)
                .join(RankedAccount, RankedAccount.user_id == EngCall.member_id, isouter=True)
                .where(EngCall.token_contract == contract, EngCall.ts >= cutoff)
                .order_by(EngCall.ts.asc())
            )).all()

        if not rows:
            return empty

        # de-dup to one signal per (tweet, caller): a tweet carrying both ticker+contract of
        # the same token makes two eng_call rows but is ONE signal
        seen: set[tuple[str, str]] = set()
        n_buckets = max(1, int(lookback.total_seconds() // bucket_s))
        buzz = [0.0] * n_buckets
        top_per_bucket: list[dict[str, tuple[float, str]]] = [dict() for _ in range(n_buckets)]
        signals: dict[str, dict] = {}

        for tweet_id, ts, member_id, uname, score, followers in rows:
            if ts is None:
                continue
            key = (tweet_id, str(member_id))
            if key in seen:
                continue
            seen.add(key)
            # influence weight: sqrt-damped Loudrr score so one mega-KOL can't wholly own a
            # bucket, but a Megaphone still counts several Whispers. Unranked caller -> min.
            infl = float(score or 0.0)
            weight = 1.0 + ((infl / 1000.0) ** 0.5)
            bi = min(n_buckets - 1, int((ts - cutoff).total_seconds() // bucket_s))
            if bi < 0:
                continue
            buzz[bi] += weight
            if uname:
                cur = top_per_bucket[bi].get(uname)
                if cur is None or infl > cur[0]:
                    top_per_bucket[bi][uname] = (infl, uname)
            # per-KOL signal tally for the leaderboard
            sig = signals.get(str(member_id))
            if sig is None:
                signals[str(member_id)] = {
                    "username": uname, "score": infl,
                    "followers": int(followers or 0), "signals": 1, "weight": weight}
            else:
                sig["signals"] += 1
                sig["weight"] += weight

        peak = max(buzz) or 1.0
        base = now - timedelta(seconds=n_buckets * bucket_s)
        series = []
        for i, v in enumerate(buzz):
            tops = sorted(top_per_bucket[i].values(), reverse=True)[:5]
            series.append({
                "ts": (base + timedelta(seconds=i * bucket_s)).isoformat(),
                "buzz": round(100.0 * v / peak, 1),
                "topCallers": [u for _, u in tops],
            })
        # current buzz index = the last fully-formed bucket, 0-100
        buzz_index = series[-1]["buzz"] if series else 0.0

        ranked = sorted(signals.values(), key=lambda d: (-d["weight"], -d["signals"]))
        signal_list = [{
            "username": d["username"], "score": round(d["score"], 1),
            "tier": _tier_for(d["score"]), "followers": d["followers"],
            "signals": d["signals"],
        } for d in ranked if d["username"]][:40]

        return {**empty, "buzzIndex": buzz_index, "series": series,
                "signals": signal_list, "coverage": "tracked"}
    except Exception:  # noqa: BLE001 — degrades, never errors
        logger.exception("kol-calls buzz failed for %r", contract)
        return empty


# Canonical Loudrr tiers on the public 0-6000 scale (same cutoffs as the leaderboard/profile
# and scripts/wallet_kols_report.py) — the signal leaderboard shows the tier users already know.
_TIER_BANDS = (("Megaphone", 4500), ("Amplifier", 3400), ("Signal", 2300), ("Echo", 1200))


def _tier_for(score: float) -> str:
    for name, floor in _TIER_BANDS:
        if score >= floor:
            return name
    return "Whisper"
