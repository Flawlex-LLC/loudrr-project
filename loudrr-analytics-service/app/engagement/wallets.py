"""KOL wallet vault: import every free external mapping + extract self-posted wallets.

    python -m app.engagement.wallets            # import external sources + self-posted scan

Sources (per the 2026-07-02 research; all free, ToS-clean):
  * kol-quest (GitHub, MIT)   — ~1,300 kolscan+GMGN-derived wallets with X handles
  * KOL-Wallets-BULLX (GitHub) — top-50 kolscan wallets
  * self-posted (our bronze)   — addresses KOLs posted in their OWN tweets (highest trust)
  (Arkham / Dune / Vybe importers plug into the same ``import_external`` once keys exist.)

Contamination defense for self-posted: the dominant false positive is a shilled token
CONTRACT, not a wallet — an address is only a wallet if it (a) never appears as a called
token, (b) isn't posted by many different members, and (c) sits next to first-person
context words ("my wallet", "tip", "donate", "send").
"""
from __future__ import annotations

import asyncio
import logging
import re

import httpx
from sqlalchemy import select

from app.db.models import SmartSetMember
from app.db.session import Base, SessionLocal, engine
# Shared with calls.py on purpose: the contamination cross-check ("this address is a called
# token, not a wallet") only works while both scanners classify addresses IDENTICALLY.
from app.engagement.calls import _EVM_RE, _SOL_RE
from app.engagement.models import EngCall, EngTweetRaw, EngWallet

logger = logging.getLogger("eng.wallets")
_SELF_CONTEXT_RE = re.compile(
    r"\b(my (sol|eth|wallet|address)|wallet|tip|tips|donat\w*|send (to|here)|address:)\b", re.I)
MAX_MEMBERS_PER_ADDRESS = 2   # posted by more distinct members than this = a shilled CA

# (name, url, tier) — raw-GitHub JSON dumps; paths verified against the live repos 2026-07-02.
DEFAULT_SOURCES: list[tuple[str, str, str]] = [
    # kolscan leaderboard mirror: wallet_address + twitter URL per KOL (identity-mapped)
    ("kolscan",
     "https://raw.githubusercontent.com/nirholas/kol-quest/main/output/kolscan-leaderboard.json",
     "leaderboard"),
    # BULLX top KOL wallets: named (no handle), Solana
    ("bullx",
     "https://raw.githubusercontent.com/sn3ll/KOL-Wallets-BULLX/main/Import.json",
     "leaderboard"),
]

_TIER_RANK = {"leaderboard": 0, "curated": 1, "self": 2}


class HttpFetcher:
    async def get_json(self, url: str):
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as c:
            r = await c.get(url)
            r.raise_for_status()
            return r.json()


# Live kolscan leaderboard: kolscan embeds its ENTIRE tracked-KOL universe (~570 wallets) in
# the RSC initialData of every page — not just the ~140 rendered leaderboard rows. The wallet
# objects are {wallet_address, name, pfp, telegram, twitter, ...}; a strict field-adjacency
# regex ("name","telegram","twitter" in order) matched only the pfp-less rendered rows and
# silently dropped ~450 of them. This tolerant form ties each wallet_address to its OWN twitter
# (bounded so it never crosses into the next wallet object) — ~5x the pairs from one page load.
_KOLSCAN_OBJ = re.compile(
    r'"wallet_address":"([1-9A-HJ-NP-Za-km-z]{32,44})"'
    r'(?:(?!"wallet_address").)*?'          # stay inside this wallet's object
    r'"twitter":("[^"]*"|null)',
    re.DOTALL,
)


def scrape_kolscan_live(proxies: list[str] | None = None,
                        timeframes: tuple[str, ...] = ("1", "7", "30")) -> list[dict]:
    """Scrape kolscan.io leaderboard across timeframes -> [{handle, address, name}]. Sync
    (curl_cffi impersonation); call via asyncio.to_thread. Degrades to [] on any failure."""
    try:
        import random

        from curl_cffi import requests as cr
    except Exception:  # noqa: BLE001 — curl_cffi absent (e.g. slim image) -> skip live source
        logger.warning("curl_cffi unavailable — skipping live kolscan scrape")
        return []

    def _prox():
        if not proxies:
            return None
        p = random.choice(proxies)
        # accept both the raw Webshare "host:port:user:pass" and a ready "http://…" URL
        # (_proxy_urls now normalizes to the latter); curl_cffi wants a URL per scheme
        if not p.startswith("http"):
            h, port, u, pw = p.split(":")
            p = f"http://{u}:{pw}@{h}:{port}"
        return {"http": p, "https": p}

    seen: dict[str, dict] = {}
    for tf in timeframes:
        try:
            r = cr.get(f"https://kolscan.io/leaderboard?timeframe={tf}",
                       impersonate="chrome", proxies=_prox(), timeout=30)
            body = r.text.encode().decode("unicode_escape", "ignore")
        except Exception as e:  # noqa: BLE001
            logger.warning("kolscan live tf=%s: %s", tf, e)
            continue
        for addr, tw in _KOLSCAN_OBJ.findall(body):
            m = re.search(r"(?:x|twitter)\.com/([A-Za-z0-9_]{1,15})", tw)
            seen.setdefault(addr, {"address": addr,
                                   "handle": m.group(1) if m else None,
                                   "name": None})
    logger.info("kolscan live: %s wallets (%s handle-paired)",
                len(seen), sum(1 for v in seen.values() if v["handle"]))
    return list(seen.values())


# GMGN.ai is the only keyless EVM wallet->X-handle source (kolscan is Solana-only). Its
# tagged wallet-rank endpoints (tag=renowned/kol/smart_degen) return JSON with wallet_address
# + twitter_username across eth/base/bsc/tron/sol. Cloudflare-gated: the UNTAGGED endpoint
# 403s, but the TAGGED ones pass with curl_cffi chrome impersonation + residential proxy
# (verified 2026-07-07). Fragile by nature — degrades to [] and retries per (chain,tag,tf).
GMGN_CHAINS = ("eth", "base", "bsc", "tron", "sol", "arbitrum", "avax", "blast", "opbnb")
GMGN_TAGS = ("renowned", "kol", "smart_degen")
GMGN_TFS = ("1d", "7d", "30d")
# The rank endpoint caps a page at 200 (asking for 500 still returns 200), but `offset` pages
# deeper — so walk offsets to reach beyond the top-100 default. Depth has diminishing
# X-handle yield (prominent KOLs rank highest); the per-combo loop early-stops the moment a
# page adds no new handle-linked wallet, so listing extra offsets is free of wasted requests.
GMGN_PAGE = 200
GMGN_OFFSETS = (0, 200, 400, 600, 800)
# GMGN chain id -> our eng_wallet.chain label (EVM family collapses to "evm" for ride matching)
_GMGN_CHAIN = {"eth": "evm", "base": "evm", "bsc": "evm", "arb": "evm", "arbitrum": "evm",
               "tron": "tron", "sol": "sol"}


def scrape_gmgn_wallets(proxies: list[str] | None = None) -> list[dict]:
    """Scrape GMGN renowned/KOL wallet ranks -> [{handle, address, chain, source}] across
    chains. Sync (curl_cffi); call via asyncio.to_thread. Degrades to [] on failure."""
    try:
        import random

        from curl_cffi import requests as cr
    except Exception:  # noqa: BLE001
        logger.warning("curl_cffi unavailable — skipping GMGN scrape")
        return []

    def _prox():
        if not proxies:
            return None
        p = random.choice(proxies)
        if not p.startswith("http"):        # raw "host:port:user:pass" -> URL
            h, port, u, pw = p.split(":")
            p = f"http://{u}:{pw}@{h}:{port}"
        return {"http": p, "https": p}

    hdr = {"Referer": "https://gmgn.ai/", "Origin": "https://gmgn.ai",
           "Accept": "application/json, text/plain, */*"}
    out: dict[tuple[str, str], dict] = {}
    for chain in GMGN_CHAINS:
        label = _GMGN_CHAIN.get(chain, "evm")
        for tag in GMGN_TAGS:
            for tf in GMGN_TFS:
                for offset in GMGN_OFFSETS:
                    url = (f"https://gmgn.ai/defi/quotation/v1/rank/{chain}/wallets/{tf}"
                           f"?tag={tag}&orderby=pnl_{tf}&direction=desc"
                           f"&limit={GMGN_PAGE}&offset={offset}")
                    r = None
                    for _ in range(3):
                        try:
                            r = cr.get(url, impersonate="chrome", proxies=_prox(),
                                       headers=hdr, timeout=30)
                            if r.status_code == 200:
                                break
                        except Exception:  # noqa: BLE001 — proxy hiccup, retry
                            r = None
                    if not r or r.status_code != 200:
                        continue
                    try:
                        data = r.json().get("data", {})
                    except Exception:  # noqa: BLE001
                        continue
                    rank = data.get("rank") or data.get("list") or (data if isinstance(data, list) else [])
                    rank = rank if isinstance(rank, list) else []
                    new_here = 0
                    for w in rank:
                        tw = (w.get("twitter_username") or "").strip().lstrip("@")
                        addr = w.get("wallet_address") or w.get("address")
                        if not (tw and addr):
                            continue
                        address = addr.lower() if label == "evm" else addr
                        key = (label, address)
                        if key not in out:
                            new_here += 1
                        out.setdefault(key, {"handle": tw.lower(), "address": address,
                                             "chain": label, "source": "gmgn"})
                    # deeper pages that add nothing new (or an empty page) end this combo —
                    # the tail is unranked/handle-less, no point walking it
                    if not rank or (offset > 0 and new_here == 0):
                        break
    logger.info("gmgn: %s handle-linked wallets across chains", len(out))
    return list(out.values())


# GMGN per-token top-trader endpoint — the LONG TAIL the curated tag-rank can't reach. Each hot
# token exposes ~100 top traders, a couple of which carry a twitter_username; iterating many hot
# tokens accumulates handle-linked wallets the rank scrape never lists. Bounded per run.
GMGN_TRADER_CHAINS = ("sol", "eth", "base", "bsc")
GMGN_TRADER_TFS = ("1h", "6h", "24h")
GMGN_TRADER_MAX_TOKENS = 400          # cap token_traders calls/run (each is one request)
# device_id just has to be a stable UUID-shaped value; the endpoint 404s to HTML without it.
_GMGN_COMMON = ("device_id=520d5e5c-1b3f-4a5f-9d3c-a1b2c3d4e5f6&client_id=gmgn_web_20240"
                "&from_app=gmgn&app_ver=20240&tz_name=UTC&tz_offset=0&app_lang=en")


def scrape_gmgn_token_traders(proxies: list[str] | None = None,
                              extra_tokens: list[tuple[str, str]] | None = None) -> list[dict]:
    """Hot tokens -> per-token top traders -> handle-linked wallets. Sync (curl_cffi); call via
    asyncio.to_thread. Degrades to [] on failure. extra_tokens = [(chain_label, address)] to
    also mine specific tokens (e.g. our KOL-called eng_token set)."""
    try:
        import random

        from curl_cffi import requests as cr
    except Exception:  # noqa: BLE001
        logger.warning("curl_cffi unavailable — skipping GMGN token_traders scrape")
        return []

    hdr = {"Referer": "https://gmgn.ai/", "Origin": "https://gmgn.ai",
           "Accept": "application/json, text/plain, */*"}

    def _prox():
        if not proxies:
            return None
        p = random.choice(proxies)
        if not p.startswith("http"):
            h, port, u, pw = p.split(":")
            p = f"http://{u}:{pw}@{h}:{port}"
        return {"http": p, "https": p}

    def _get(url):
        for _ in range(3):
            try:
                r = cr.get(url, impersonate="chrome", proxies=_prox(), headers=hdr, timeout=30)
                if r.status_code == 200:
                    return r.json().get("data", {})
            except Exception:  # noqa: BLE001 — proxy hiccup, retry a different exit
                pass
        return None

    # 1) collect hot token addresses per chain (swaps rank) + any caller-supplied tokens
    tokens: list[tuple[str, str]] = list(extra_tokens or [])   # (chain, address)
    seen_tok: set[tuple[str, str]] = set(tokens)
    for chain in GMGN_TRADER_CHAINS:
        label = _GMGN_CHAIN.get(chain, "evm")
        for tf in GMGN_TRADER_TFS:
            data = _get(f"https://gmgn.ai/defi/quotation/v1/rank/{chain}/swaps/{tf}"
                        f"?orderby=swaps&direction=desc&limit=100&{_GMGN_COMMON}")
            rank = (data or {}).get("rank") or (data or {}).get("list") or []
            for t in rank if isinstance(rank, list) else []:
                a = t.get("address") or t.get("token_address")
                if a and (label, a) not in seen_tok:
                    seen_tok.add((label, a))
                    tokens.append((label, a))
    # bound the number of token_traders calls per run
    tokens = tokens[:GMGN_TRADER_MAX_TOKENS]

    # 2) per token, pull top traders and keep the handle-linked ones
    out: dict[tuple[str, str], dict] = {}
    for label, token in tokens:
        path_chain = "sol" if label == "sol" else "eth"  # EVM top-traders share the eth route id
        data = _get(f"https://gmgn.ai/vas/api/v1/token_traders/{path_chain}/{token}"
                    f"?limit=100&{_GMGN_COMMON}")
        lst = (data or {}).get("list") or (data or {}).get("rank") or []
        for w in lst if isinstance(lst, list) else []:
            tw = (w.get("twitter_username") or "").strip().lstrip("@")
            addr = w.get("address") or w.get("account_address")
            if not (tw and addr):
                continue
            wl = _chain_of(addr)                 # trust the address shape, not the token's chain
            if wl is None:
                continue
            address = addr.lower() if wl == "evm" else addr
            out.setdefault((wl, address), {"handle": tw.lower(), "address": address,
                                           "chain": wl, "source": "gmgn_traders"})
    logger.info("gmgn token_traders: %s handle-linked wallets from %s tokens",
                len(out), len(tokens))
    return list(out.values())


def _chain_of(address: str) -> str | None:
    if _EVM_RE.fullmatch(address):
        return "evm"
    if _SOL_RE.fullmatch(address) and not address.isdigit():
        return "sol"
    return None


def _entries(payload) -> list[dict]:
    """Normalize messy external shapes into {handle?, address} dicts."""
    if isinstance(payload, dict):
        for key in ("wallets", "data", "items", "kols", "traders"):
            if isinstance(payload.get(key), list):
                payload = payload[key]
                break
        else:
            payload = list(payload.values())
    out = []
    for e in payload if isinstance(payload, list) else []:
        if isinstance(e, str):
            out.append({"handle": None, "address": e.strip()})
            continue
        if not isinstance(e, dict):
            continue
        addr = next((str(e[k]).strip() for k in
                     ("wallet", "address", "wallet_address", "walletAddress", "account")
                     if e.get(k)), None)
        handle = next((str(e[k]).strip() for k in
                       ("twitter", "handle", "x", "twitter_handle", "twitterHandle", "username")
                       if e.get(k)), None)
        if handle and ("/" in handle):  # "https://x.com/Ansem" -> "Ansem"
            handle = handle.rstrip("/").rsplit("/", 1)[-1].split("?")[0]
        if addr:
            out.append({"handle": handle, "address": addr})
    return out


async def _member_ids_by_handle(s) -> dict[str, str]:
    rows = (await s.execute(
        select(SmartSetMember.user_id, SmartSetMember.username)
        .where(SmartSetMember.username.is_not(None)))).all()
    return {str(u).lower(): str(uid) for uid, u in rows if u}


async def _store(s, claims: list[dict]) -> int:
    """Insert (address, handle) claims; dedupe against DB; upgrade confidence in place."""
    existing = {(w.address, w.handle): w
                for w in (await s.execute(select(EngWallet))).scalars().all()}
    inserted = 0
    seen: set[tuple[str, str | None]] = set()
    for c in claims:
        key = (c["address"], c["handle"])
        if key in seen:
            continue
        seen.add(key)
        cur = existing.get(key)
        if cur is not None:
            if _TIER_RANK.get(c["confidence"], 0) > _TIER_RANK.get(cur.confidence, 0):
                cur.confidence, cur.source = c["confidence"], c["source"]
            continue
        s.add(EngWallet(**c))
        inserted += 1
    return inserted


async def import_external(*, sources: list[tuple[str, str, str]] | None = None,
                          fetcher=None) -> dict:
    """Pull each configured external dump into the vault. Idempotent."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    fetcher = fetcher or HttpFetcher()
    sources = sources if sources is not None else DEFAULT_SOURCES

    imported = 0
    per_source: dict[str, int] = {}
    async with SessionLocal() as s:
        members = await _member_ids_by_handle(s)
        claims: list[dict] = []
        for name, url, tier in sources:
            try:
                payload = await fetcher.get_json(url)
            except Exception as e:  # noqa: BLE001 — one dead source must not kill the import
                logger.warning("wallet source %s (%s): %s", name, url, e)
                per_source[name] = -1
                continue
            n = 0
            for e in _entries(payload):
                chain = _chain_of(e["address"])
                if chain is None:
                    continue
                # EVM lowercased everywhere (matches calls.py / eng_call.contract);
                # Solana base58 stays case-sensitive
                address = e["address"].lower() if chain == "evm" else e["address"]
                handle = (e["handle"] or "").lstrip("@").lower() or None
                claims.append({
                    "member_id": members.get(handle) if handle else None,
                    "handle": handle, "address": address, "chain": chain,
                    "source": name, "confidence": tier,
                })
                n += 1
            per_source[name] = n
        imported = await _store(s, claims)
        await s.commit()
    return {"imported": imported, "per_source": per_source}


async def import_live_scrapes() -> dict:
    """Pull the LIVE leaderboards (kolscan + GMGN) into the vault, not just the GitHub mirrors.

    The mirrors are periodic dumps and lag newly-ranked KOLs; scraping the sites live keeps the
    watched set current — which directly grows realtime ride coverage. Sync scrapers run off the
    event loop; each degrades to [] independently, so one dead source never blocks the other.
    """
    from app.engagement.models import EngToken
    from app.engagement.onchain import _proxy_urls
    proxies = _proxy_urls() or None

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # our KOL-called tokens are prime token_traders seeds — whoever tops the traders of a token
    # a KOL called is disproportionately likely to be a KOL too
    async with SessionLocal() as s:
        called = (await s.execute(select(EngToken.contract, EngToken.chain))).all()
    extra_tokens = [("sol" if (c or "").lower() in ("sol", "solana") else "evm", addr)
                    for addr, c in called if addr]

    kol = await asyncio.to_thread(scrape_kolscan_live, proxies)
    gmgn = await asyncio.to_thread(scrape_gmgn_wallets, proxies)
    traders = await asyncio.to_thread(scrape_gmgn_token_traders, proxies, extra_tokens)

    claims: list[dict] = []
    async with SessionLocal() as s:
        members = await _member_ids_by_handle(s)
        for r in kol:  # {address, handle, name} — kolscan is Solana
            chain = _chain_of(r["address"])
            if chain is None:
                continue
            handle = (r.get("handle") or "").lstrip("@").lower() or None
            claims.append({"member_id": members.get(handle) if handle else None,
                           "handle": handle, "address": r["address"], "chain": chain,
                           "source": "kolscan", "confidence": "leaderboard"})
        for r in gmgn + traders:  # {handle, address, chain, source}
            chain = _chain_of(r["address"])
            if chain is None:
                continue
            address = r["address"].lower() if chain == "evm" else r["address"]
            handle = (r.get("handle") or "").lstrip("@").lower() or None
            claims.append({"member_id": members.get(handle) if handle else None,
                           "handle": handle, "address": address, "chain": chain,
                           "source": r.get("source", "gmgn"), "confidence": "leaderboard"})
        inserted = await _store(s, claims)
        await s.commit()
    return {"inserted": inserted, "kolscan": len(kol), "gmgn": len(gmgn),
            "gmgn_traders": len(traders)}


async def extract_self_posted() -> dict:
    """Scan bronze for wallets the members posted THEMSELVES (tier 'self'). Zero network."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    found = 0
    async with SessionLocal() as s:
        called = set((await s.execute(
            select(EngCall.contract).where(EngCall.contract.is_not(None)).distinct()
        )).scalars().all())
        members = await _member_ids_by_handle(s)
        handle_of = {v: k for k, v in members.items()}

        # candidate addresses + who posted them (streamed in batches by rowid-ish paging)
        posters: dict[str, set[str]] = {}          # address -> member_ids that posted it
        contexts: dict[tuple[str, str], bool] = {}  # (address, member) -> had self-context
        offset = 0
        while True:
            rows = (await s.execute(
                select(EngTweetRaw.member_id, EngTweetRaw.text)
                .order_by(EngTweetRaw.tweet_id).offset(offset).limit(2000))).all()
            if not rows:
                break
            offset += len(rows)
            for member_id, text in rows:
                text = text or ""
                evm_found = _EVM_RE.findall(text)
                # lowercase EVM to match calls.py's stored form — the `called` contamination
                # check below compares against eng_call.contract which is lowercased
                addrs = {a.lower() for a in evm_found}
                masked = text
                for a in evm_found:
                    masked = masked.replace(a, " " * len(a))
                addrs |= {a for a in _SOL_RE.findall(masked) if not a.isdigit()}
                if not addrs:
                    continue
                has_ctx = bool(_SELF_CONTEXT_RE.search(text))
                for a in addrs:
                    posters.setdefault(a, set()).add(str(member_id))
                    key = (a, str(member_id))
                    contexts[key] = contexts.get(key, False) or has_ctx

        claims = []
        for addr, who in posters.items():
            if addr in called or len(who) > MAX_MEMBERS_PER_ADDRESS:
                continue  # a called/shilled token contract, not a wallet
            chain = _chain_of(addr)
            if chain is None:
                continue
            for member_id in who:
                if not contexts.get((addr, member_id)):
                    continue  # no first-person context -> treat as a CA mention
                claims.append({
                    "member_id": member_id, "handle": handle_of.get(member_id),
                    "address": addr, "chain": chain,
                    "source": "tweet", "confidence": "self",
                })
        found = await _store(s, claims)
        await s.commit()
    return {"wallets_found": found, "addresses_seen": len(posters)}


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ext = await import_external()
    live = await import_live_scrapes()
    own = await extract_self_posted()
    print({"external": ext, "live": live, "self_posted": own})


if __name__ == "__main__":
    asyncio.run(main())
