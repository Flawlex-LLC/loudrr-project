"""Realtime KOL wallet watcher — the onchain half of "which KOL purchased".

WHY THIS EXISTS: the old capture (onchain.capture_rides) watched a TOKEN's pool trades and
hoped a vault wallet happened to be among them. It never was — eng_ride sat at 0 with 544
wallets loaded. This inverts it: subscribe to each KOL WALLET directly on the free keyless
Solana RPC websocket (logsSubscribe with a `mentions` filter), so every KOL swap is caught the
moment it lands, whatever token it is.

    Solana RPC ws --logsSubscribe(wallet)--> signature
                                              |
                        getTransaction(sig) --+--> parse_swap --> EngRide --> hub.push_ride
                                                                                    |
                                                                              browser (live)

FREE, verified 2026-07-16: mainnet-beta accepted 62 subscriptions on one socket, keyless, and
streamed notifications immediately. getTransaction is HTTP and rate-limited, so signatures are
de-duplicated and fetched by a small paced worker pool.

Solana only for v1 (kolscan/GMGN KOL wallets are overwhelmingly Solana). EVM wallets would add
an eth_subscribe watcher on the same hub — a later addition, not a rewrite.

OFF by default (WALLET_WATCH_ENABLED). Degrades on every axis: a dropped socket reconnects, a
dead RPC skips the fetch, an unresolved token still records the ride. Never raises into the app.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from datetime import datetime, timezone

import httpx
from aiolimiter import AsyncLimiter
from sqlalchemy import select

from app.core.config import settings
from app.db.session import SessionLocal
from app.engagement.live import hub
from app.engagement.models import EngRide, EngToken, EngWallet

logger = logging.getLogger("eng.wallets.live")

WSOL = "So11111111111111111111111111111111111111112"
# stablecoins are the quote side of a swap, never the "token traded" — exclude alongside WSOL
_QUOTE_MINTS = {
    WSOL,
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
}


def _sol_delta(tx: dict, owner: str) -> float | None:
    """Native-SOL change for `owner` (SOL, fee included), or None if not locatable."""
    msg = (tx.get("transaction") or {}).get("message") or {}
    keys = []
    for k in msg.get("accountKeys") or []:
        keys.append(k if isinstance(k, str) else k.get("pubkey"))
    if owner not in keys:
        return None
    i = keys.index(owner)
    meta = tx.get("meta") or {}
    pre, post = meta.get("preBalances") or [], meta.get("postBalances") or []
    if i >= len(pre) or i >= len(post):
        return None
    return (post[i] - pre[i]) / 1e9


def parse_swap(tx: dict, owner: str) -> dict | None:
    """Reduce a parsed Solana transaction to one KOL swap, or None if it isn't one.

    Designed against real KOL txs (scratchpad probe): a BUY shows the non-quote mint's owner
    balance rising while native SOL falls; a SELL is the mirror. We take the non-quote mint
    with the largest absolute balance change as the token traded — routing hops touch many
    accounts, but the KOL's own position moves in exactly one token.

    Returns {mint, side, token_amount, sol_amount, block_time} or None (failed tx, non-swap,
    or a pure SOL/stable transfer with no token leg).
    """
    meta = tx.get("meta") or {}
    if meta.get("err"):
        return None  # reverted — not a trade

    # NET position change per (owner, mint) — SUM across all of the owner's token accounts for
    # a mint, don't key by mint alone. An owner legitimately holds several accounts per mint
    # (an existing ATA plus a fresh temp account a router opens for the swap); keying by mint
    # kept only the last one, which could compare two DIFFERENT physical accounts pre-vs-post
    # and flip buy<->sell or drop the trade. The net delta is what "did the KOL's stack grow".
    def _net(rows) -> dict[str, float]:
        agg: dict[str, float] = {}
        for b in rows or []:
            if b.get("owner") != owner:
                continue
            amt = float((b.get("uiTokenAmount") or {}).get("uiAmount") or 0.0)
            agg[b["mint"]] = agg.get(b["mint"], 0.0) + amt
        return agg

    pre = _net(meta.get("preTokenBalances"))
    post = _net(meta.get("postTokenBalances"))

    # The token traded = the largest net move among non-quote mints. For the dominant case (a
    # SOL/stable <-> token swap) exactly one non-quote mint moves, so this is exact. For a
    # direct token<->token swap both legs are non-quote and the raw-uiAmount comparison across
    # different decimals is only a best-effort pick — acceptable for the realtime signal, and
    # such trades are rare vs. SOL-paired ones.
    best_mint, best_delta = None, 0.0
    for mint in set(pre) | set(post):
        if mint in _QUOTE_MINTS:
            continue
        d = post.get(mint, 0.0) - pre.get(mint, 0.0)
        if abs(d) > abs(best_delta):
            best_mint, best_delta = mint, d

    if best_mint is None or abs(best_delta) <= 1e-12:
        return None  # no token leg for this owner — transfer, mint, stake, etc.

    sd = _sol_delta(tx, owner)
    return {
        "mint": best_mint,
        "side": "buy" if best_delta > 0 else "sell",
        "token_amount": abs(best_delta),
        "sol_amount": abs(sd) if sd is not None else None,
        "block_time": tx.get("blockTime"),
    }


async def _resolve_token(s, mint: str, dex) -> EngToken | None:
    """eng_token for a mint — reuse the stored one, else best-effort DexScreener resolve.

    Kept off the critical path's failure surface: an unresolvable mint just means the ride
    is stored with a null price and shows in the global feed as the raw token — never a crash.
    """
    tok = await s.get(EngToken, mint)
    if tok is not None:
        return tok
    if dex is None:
        return None
    try:
        from app.engagement.onchain import _pick_pair, _token_from_pair, _upsert_token
        pairs = await dex.token_pairs(mint)
        pick = _pick_pair(pairs)  # contract path: deepest pool of THIS token, no symbol spoof
        if pick is None:
            return None
        return await _upsert_token(s, _token_from_pair(pick))
    except Exception:  # noqa: BLE001 — resolution is best-effort
        logger.debug("resolve %s failed", mint, exc_info=True)
        return None


async def ingest_signature(signature: str, wallet: EngWallet, tx: dict, *, dex=None) -> dict | None:
    """Persist one KOL swap as an EngRide and return its broadcast payload (or None).

    Idempotent on tx_hash, so a signature that notifies several watched wallets, or a replay,
    is stored once. Returns (contract, ride_dict) only for a NEWLY inserted, resolvable ride —
    that's exactly what should surface on the live socket.
    """
    swap = parse_swap(tx, wallet.address)
    if swap is None:
        return None
    ts = None
    if swap["block_time"]:
        ts = datetime.fromtimestamp(int(swap["block_time"]), tz=timezone.utc).replace(tzinfo=None)

    async with SessionLocal() as s:
        if (await s.execute(
            select(EngRide.id).where(EngRide.tx_hash == signature).limit(1)
        )).scalars().first():
            return None  # already captured

        tok = await _resolve_token(s, swap["mint"], dex)
        price = tok.price_usd if tok is not None else None
        volume = price * swap["token_amount"] if price is not None else None
        contract = tok.contract if tok is not None else swap["mint"]

        s.add(EngRide(
            token_contract=contract, wallet_address=wallet.address,
            member_id=wallet.member_id, handle=wallet.handle, side=swap["side"],
            price_usd=price, volume_usd=volume, ts=ts, tx_hash=signature,
        ))
        try:
            await s.commit()
        except Exception:  # noqa: BLE001 — unique race: another fetcher won, that's fine
            await s.rollback()
            return None

    # tier so a live ride renders identically to the REST chart rides (same detail card)
    from app.engagement.api import _tier_for
    return (contract, {
        "username": wallet.handle, "side": swap["side"],
        "ts": ts.isoformat() if ts else None,
        "price": price, "volumeUsd": volume,
        "score": wallet.score,
        "tier": _tier_for(wallet.score) if wallet.score is not None else None,
        "smartFollowers": wallet.smart_followers,
        "mint": swap["mint"], "tokenAmount": swap["token_amount"],
    })


async def _watched_wallets() -> dict[str, EngWallet]:
    """Solana vault wallets to watch, highest Loudrr score first, address-keyed."""
    async with SessionLocal() as s:
        q = (select(EngWallet)
             .where(EngWallet.chain == "sol", EngWallet.handle.is_not(None))
             .order_by(EngWallet.score.desc().nulls_last()))
        if settings.wallet_watch_max:
            q = q.limit(settings.wallet_watch_max)
        rows = (await s.execute(q)).scalars().all()
    # de-dup by address (one wallet can carry several handle rows); first = highest score
    out: dict[str, EngWallet] = {}
    for w in rows:
        out.setdefault(w.address, w)
    return out


def _watch_proxies() -> list[str]:
    """Webshare exit IPs for the watcher, as ``http://user:pass@host:port``.

    THE scaling mechanism: the free public RPC caps ~60 subscriptions per connection AND
    connections-per-IP, so one machine 429s past a few hundred wallets. Giving each connection
    a distinct proxy IP (and rotating getTransaction across the pool) means every IP stays under
    its own budget — verified: 30 connections through 30 proxies = 0 rejections. Prefers the
    ONCHAIN_PROXIES env (prod), falls back to the on-disk Webshare file (local/dev). Empty =>
    direct, and the watch cap stays low.
    """
    from app.engagement.onchain import _proxy_urls
    proxies = _proxy_urls()
    if proxies:
        return proxies
    import os
    path = os.environ.get("WEBSHARE_FILE", "data/proxies/webshare.txt")
    out: list[str] = []
    if os.path.exists(path):
        for ln in open(path, encoding="utf-8"):
            ln = ln.strip()
            if not ln or ln.startswith("#"):
                continue
            if ln.startswith("http"):
                out.append(ln)
            else:
                p = ln.split(":")
                if len(p) == 4:                       # Webshare host:port:user:pass
                    out.append(f"http://{p[2]}:{p[3]}@{p[0]}:{p[1]}")
    return out


class SolanaWalletWatcher:
    """logsSubscribe fan-in across proxied sockets -> paced getTransaction -> EngRide + broadcast.

    Each socket exits through a distinct Webshare IP (one connection per proxy), so the free
    RPC's per-IP limit never bites and the watch cap scales with the proxy pool (~subs_per_conn
    wallets per proxy). getTransaction rotates across the same pool for the same reason.
    """

    def __init__(self, wallets: dict[str, EngWallet]):
        self._wallets = wallets
        self._q: asyncio.Queue[tuple[str, str]] = asyncio.Queue(maxsize=2000)
        # dict (not set) as an insertion-ordered dedup: eviction must keep the MOST-RECENT
        # signatures, and list(set)[-N:] slices hash order, retaining arbitrary keys.
        self._recent: dict[str, None] = {}
        self._limiter = AsyncLimiter(settings.wallet_watch_rpc_per_min, 60)
        self._proxies = _watch_proxies()
        self._clients: dict[str | None, httpx.AsyncClient] = {}  # per-exit getTransaction pool
        self._rr = 0

    def _rpc_client(self, proxy: str | None) -> httpx.AsyncClient:
        c = self._clients.get(proxy)
        if c is None or c.is_closed:
            c = httpx.AsyncClient(timeout=20.0, proxy=proxy) if proxy \
                else httpx.AsyncClient(timeout=20.0)
            self._clients[proxy] = c
        return c

    async def run(self) -> None:
        if not self._wallets:
            logger.warning("wallet watcher: no Solana vault wallets to watch")
            return
        addrs = list(self._wallets)
        per = max(1, settings.wallet_watch_subs_per_conn)
        chunks = [addrs[i:i + per] for i in range(0, len(addrs), per)]
        n_px = len(self._proxies)
        if n_px:
            logger.info("wallet watcher: %d wallets, %d socket(s), %d Webshare exits "
                        "(1 conn/proxy)", len(addrs), len(chunks), n_px)
            if len(chunks) > n_px:
                logger.warning("more sockets (%d) than proxies (%d) — some IPs carry >1 "
                               "connection and may throttle; add proxies or raise "
                               "WALLET_WATCH_SUBS_PER_CONN", len(chunks), n_px)
        else:
            logger.warning("wallet watcher: NO proxies — direct from one IP, the RPC will "
                           "429 past a few hundred wallets. Set ONCHAIN_PROXIES/WEBSHARE_FILE.")
        try:
            # each socket gets its own exit IP (round-robin if sockets > proxies)
            tasks = [asyncio.create_task(
                self._socket(c, self._proxies[i % n_px] if n_px else None))
                for i, c in enumerate(chunks)]
            tasks += [asyncio.create_task(self._fetcher())
                      for _ in range(settings.wallet_watch_fetchers)]
            await asyncio.gather(*tasks)
        finally:
            for c in self._clients.values():
                with contextlib.suppress(Exception):
                    await c.aclose()

    async def _socket(self, addrs: list[str], proxy: str | None) -> None:
        """Hold one ws connection (via `proxy`) subscribing to `addrs`; reconnect with backoff."""
        import websockets
        backoff = 1.0
        while True:
            try:
                async with websockets.connect(settings.solana_ws_url, open_timeout=25,
                                              ping_interval=20, max_queue=512,
                                              proxy=proxy) as ws:
                    for i, addr in enumerate(addrs):
                        await ws.send(json.dumps({
                            "jsonrpc": "2.0", "id": i + 1, "method": "logsSubscribe",
                            "params": [{"mentions": [addr]}, {"commitment": "confirmed"}],
                        }))
                    submap: dict[int, str] = {}
                    pending = {i + 1: addr for i, addr in enumerate(addrs)}
                    backoff = 1.0  # a clean connect resets the backoff
                    async for raw in ws:
                        msg = json.loads(raw)
                        rid = msg.get("id")
                        if rid in pending and isinstance(msg.get("result"), int):
                            submap[msg["result"]] = pending.pop(rid)
                            continue
                        if msg.get("method") != "logsNotification":
                            continue
                        val = (((msg.get("params") or {}).get("result") or {}).get("value") or {})
                        if val.get("err"):
                            continue  # failed tx — never a trade
                        sig = val.get("signature")
                        addr = submap.get((msg.get("params") or {}).get("subscription"))
                        if sig and addr and sig not in self._recent:
                            # enqueue FIRST, mark seen only on success — marking before the
                            # put meant a QueueFull drop was remembered as "seen" and the
                            # signature was then never processable again.
                            try:
                                self._q.put_nowait((sig, addr))
                            except asyncio.QueueFull:
                                logger.warning("wallet sig queue full — dropping %s", sig[:16])
                                continue
                            self._recent[sig] = None
                            if len(self._recent) > 8000:
                                self._recent = dict.fromkeys(list(self._recent)[-4000:])
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — drop, wait, reconnect
                logger.warning("wallet socket dropped (%d addrs), reconnecting in %.0fs",
                               len(addrs), backoff, exc_info=True)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    async def _fetcher(self) -> None:
        """Pull signatures, getTransaction (paced), ingest, broadcast."""
        from app.engagement.onchain import DexScreenerClient
        dex = DexScreenerClient()
        try:
            await self._fetch_loop(dex)
        finally:
            with contextlib.suppress(Exception):
                await dex.aclose()

    async def _fetch_loop(self, dex) -> None:
        while True:
            sig, addr = await self._q.get()
            wallet = self._wallets.get(addr)
            if wallet is None:
                continue
            try:
                await self._limiter.acquire()
                # rotate the exit IP per call — getTransaction shares the RPC's per-IP budget,
                # so a single client would 429 at the same volume the sockets do
                proxy = self._proxies[self._rr % len(self._proxies)] if self._proxies else None
                self._rr += 1
                r = await self._rpc_client(proxy).post(settings.solana_rpc_url, json={
                    "jsonrpc": "2.0", "id": 1, "method": "getTransaction",
                    "params": [sig, {"maxSupportedTransactionVersion": 0, "encoding": "jsonParsed"}],
                })
                r.raise_for_status()
                tx = r.json().get("result")
                if not tx:
                    continue
                payload = await ingest_signature(sig, wallet, tx, dex=dex)
                if payload is not None:
                    contract, ride = payload
                    await hub.push_ride(contract, ride)
                    logger.info("ride: @%s %s %s", wallet.handle, ride["side"], contract[:8])
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — one bad signature never stops the stream
                logger.debug("fetch %s failed", sig[:16], exc_info=True)


async def run_wallet_watcher() -> None:
    """Entry point (worker / standalone). No-op unless WALLET_WATCH_ENABLED."""
    if not settings.wallet_watch_enabled:
        logger.info("wallet watcher disabled (set WALLET_WATCH_ENABLED=1)")
        return
    wallets = await _watched_wallets()
    await SolanaWalletWatcher(wallets).run()


if __name__ == "__main__":
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(message)s")
    settings.wallet_watch_enabled = True  # explicit standalone run
    asyncio.run(run_wallet_watcher())
