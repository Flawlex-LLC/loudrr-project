"""TDD spec — KOL Rides: tracked-wallet trades on a token (the onchain half of Signals).

Rides come from keyless pool-trade feeds intersected with the eng_wallet vault; every
capture PERSISTS (unique tx_hash) so ride history accumulates beyond the feed's window.
Only identity-mapped wallets (handle present) become rides — anonymous vault entries
can't be pinned to a KOL and are skipped.
"""
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.engagement import models as em
from app.engagement.onchain import capture_rides

SOL_CA = "9ybu4ArAY9iyGpjh99eYSmjn5tw3Jvyo9aeRFQqy6Ezh"
POOL = "PoolAddr111111111111111111111111111111111111"
W_KOL = "5ueY3fD8uDJgsJRq12XaER13JzsaV5NDwFmjEs3FJ39Z"     # tracked, identity-mapped
W_ANON = "An0nWa11et111111111111111111111111111111111"      # tracked, no handle
W_RANDO = "Rand0Wa11et11111111111111111111111111111111"     # not in vault


def _trade(tx, addr, kind="buy", price="0.02", ts="2026-07-02T13:09:53Z", vol="150.5"):
    return {"tx_hash": tx, "tx_from_address": addr, "kind": kind,
            "block_timestamp": ts, "price_to_in_usd": price, "volume_in_usd": vol}


class FakeGecko:
    def __init__(self, trades):
        self._trades = trades
        self.calls = []

    async def trades(self, network, pool):
        self.calls.append((network, pool))
        return self._trades


async def _seed_wallets(SessionLocal):
    async with SessionLocal() as s:
        s.add(em.EngWallet(member_id="1", handle="ansem", address=W_KOL, chain="sol",
                           source="kolscan", confidence="leaderboard"))
        s.add(em.EngWallet(member_id=None, handle=None, address=W_ANON, chain="sol",
                           source="bullx", confidence="leaderboard"))
        await s.commit()


async def test_capture_intersects_vault_and_persists(eng_db):
    await _seed_wallets(eng_db)
    gecko = FakeGecko([
        _trade("tx1", W_KOL, kind="buy"),
        _trade("tx2", W_RANDO),               # not tracked -> ignored
        _trade("tx3", W_ANON),                # tracked but anonymous -> ignored
        _trade("tx4", W_KOL, kind="sell", price="0.03"),
    ])
    stats = await capture_rides(SOL_CA, "solana", POOL, gecko=gecko)
    assert stats["rides_inserted"] == 2
    async with eng_db() as s:
        rides = (await s.execute(select(em.EngRide).order_by(em.EngRide.tx_hash))).scalars().all()
        assert [(r.tx_hash, r.side, r.handle) for r in rides] == \
            [("tx1", "buy", "ansem"), ("tx4", "sell", "ansem")]
        assert rides[0].price_usd == pytest.approx(0.02)
        assert rides[0].token_contract == SOL_CA
        assert rides[0].ts == datetime(2026, 7, 2, 13, 9, 53)   # UTC-naive


async def test_capture_is_idempotent_by_tx(eng_db):
    await _seed_wallets(eng_db)
    gecko = FakeGecko([_trade("tx1", W_KOL)])
    await capture_rides(SOL_CA, "solana", POOL, gecko=gecko)
    stats = await capture_rides(SOL_CA, "solana", POOL, gecko=gecko)
    assert stats["rides_inserted"] == 0
    async with eng_db() as s:
        assert len((await s.execute(select(em.EngRide))).scalars().all()) == 1


async def test_capture_degrades_on_feed_failure(eng_db):
    class Dead:
        async def trades(self, *_a, **_k):
            raise RuntimeError("gecko down")
    stats = await capture_rides(SOL_CA, "solana", POOL, gecko=Dead())
    assert stats["rides_inserted"] == 0             # never raises
