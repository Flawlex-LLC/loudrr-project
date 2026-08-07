"""Build the KOL wallet vault on PROD from every free source, mapped to our scored accounts.

Sources merged (highest trust wins on conflict — self > curated > leaderboard):
  * live kolscan.io  (proxied scrape, current leaderboard, handle-paired)
  * kol-quest GitHub  (~1,300 kolscan/GMGN-derived, handle-paired)
  * BULLX GitHub      (top-50 Solana, named)
  * self-posted       (addresses members posted in their OWN prod tweets — highest trust)
  * local vault       (data/harvest.db eng_wallet, accumulated pre-prod)
  * EVM sources        plug in via EXTRA_SOURCES once the scout confirms endpoints

Every handle is remapped to prod smart_set.user_id (33.8k ranked handles now vs 50 before),
so KOL Rides can show each wallet's Loudrr score/tier. Idempotent + rerunnable.

    DATABASE_URL=<prod> WEBSHARE_PROXIES="<path>" python -m scripts.import_wallets_prod
"""
from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import sys

sys.path.insert(0, ".")

from sqlalchemy import func, select  # noqa: E402

from app.db.models import SmartSetMember  # noqa: E402
from app.db.session import Base, SessionLocal, engine  # noqa: E402
from app.engagement.models import EngWallet  # noqa: E402
from app.engagement.wallets import (  # noqa: E402
    DEFAULT_SOURCES,
    _chain_of,
    _member_ids_by_handle,
    _store,
    extract_self_posted,
    import_external,
    scrape_gmgn_wallets,
    scrape_kolscan_live,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    stream=sys.stdout)
log = logging.getLogger("import_wallets")

LOCAL_DB = os.environ.get("LOCAL_HARVEST_DB", "data/harvest.db")


def _proxies() -> list[str]:
    path = os.environ.get("WEBSHARE_PROXIES") or os.environ.get("TS_PROXIES_FILE")
    if path and os.path.exists(path):
        return [l.strip() for l in open(path, encoding="utf-8") if l.strip() and l.count(":") == 3]
    return []


def _local_vault() -> list[dict]:
    """Existing eng_wallet rows from the local harvest DB (accumulated pre-prod)."""
    if not os.path.exists(LOCAL_DB):
        return []
    con = sqlite3.connect(LOCAL_DB)
    try:
        rows = con.execute(
            "select handle, address, chain, source, confidence from eng_wallet").fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()
    out = []
    for handle, address, chain, source, confidence in rows:
        if not address:
            continue
        out.append({"handle": (handle or "").lstrip("@").lower() or None,
                    "address": address, "chain": chain or _chain_of(address),
                    "source": source or "local", "confidence": confidence or "leaderboard"})
    return out


async def main() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    proxies = _proxies()
    log.info("proxies loaded: %s", len(proxies))

    # 1) GitHub mirrors (kol-quest ~1300, BULLX) — the async importer already maps + stores
    ext = await import_external(sources=DEFAULT_SOURCES)
    log.info("github mirrors: %s", ext)

    # 2) live kolscan (SOL) + GMGN (EVM/multi-chain) + local vault -> normalized claims
    live = await asyncio.to_thread(scrape_kolscan_live, proxies)
    gmgn = await asyncio.to_thread(scrape_gmgn_wallets, proxies)
    local = _local_vault()
    log.info("live kolscan: %s | gmgn: %s | local vault: %s", len(live), len(gmgn), len(local))

    async with SessionLocal() as s:
        members = await _member_ids_by_handle(s)
        claims: list[dict] = []
        for e in live:
            chain = _chain_of(e["address"])
            if chain is None:
                continue
            handle = (e["handle"] or "").lstrip("@").lower() or None
            addr = e["address"].lower() if chain == "evm" else e["address"]
            claims.append({"member_id": members.get(handle) if handle else None,
                           "handle": handle, "address": addr, "chain": chain,
                           "source": "kolscan", "confidence": "leaderboard"})
        for e in gmgn:  # chain already normalized (evm/sol/tron), EVM addr already lowercased
            handle = (e["handle"] or "").lstrip("@").lower() or None
            claims.append({"member_id": members.get(handle) if handle else None,
                           "handle": handle, "address": e["address"], "chain": e["chain"],
                           "source": "gmgn", "confidence": "leaderboard"})
        for e in local:
            chain = e["chain"] if e["chain"] in ("sol", "evm") else _chain_of(e["address"])
            if chain is None:
                continue
            handle = e["handle"]
            addr = e["address"].lower() if chain == "evm" else e["address"]
            claims.append({"member_id": members.get(handle) if handle else None,
                           "handle": handle, "address": addr, "chain": chain,
                           "source": e["source"], "confidence": e["confidence"]})
        stored = await _store(s, claims)
        await s.commit()
    log.info("live+local stored (new): %s", stored)

    # 3) self-posted from prod bronze (highest trust) — scans the 1.7M-tweet corpus
    try:
        own = await extract_self_posted()
        log.info("self-posted: %s", own)
    except Exception:  # noqa: BLE001
        log.exception("self-posted extraction failed (continuing)")

    # 4) backfill member_id for any wallet whose handle we can now resolve
    async with SessionLocal() as s:
        members = await _member_ids_by_handle(s)
        unmapped = (await s.execute(
            select(EngWallet).where(EngWallet.member_id.is_(None),
                                    EngWallet.handle.is_not(None)))).scalars().all()
        fixed = 0
        for w in unmapped:
            mid = members.get((w.handle or "").lower())
            if mid:
                w.member_id = mid
                fixed += 1
        await s.commit()

        total = (await s.execute(select(func.count()).select_from(EngWallet))).scalar()
        mapped = (await s.execute(select(func.count()).select_from(EngWallet)
                                  .where(EngWallet.member_id.is_not(None)))).scalar()
        by_chain = (await s.execute(
            select(EngWallet.chain, func.count()).group_by(EngWallet.chain))).all()
        handled = (await s.execute(select(func.count()).select_from(EngWallet)
                                   .where(EngWallet.handle.is_not(None)))).scalar()
    log.info("member_id backfilled: +%s", fixed)
    log.info("VAULT TOTAL: %s wallets | %s handle-paired | %s mapped to scored members | by chain %s",
             total, handled, mapped, dict(by_chain))


if __name__ == "__main__":
    asyncio.run(main())
