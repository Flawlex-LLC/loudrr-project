"""TDD spec — token resolution + onchain enrichment (free DexScreener-style client, mocked).

Invariants:
  * contract calls resolve directly (token metadata upserted into eng_token),
  * ticker calls resolve via search -> the TOP-LIQUIDITY pair whose symbol matches
    (case-insensitive) AND clears a minimum liquidity floor (scam-copy defense),
  * price_at_call/mcap_at_call snapshot ONLY for fresh calls (tweet younger than the
    freshness window) — stale backfilled calls stay NULL (honest data, no fake fills),
  * refresh touches only ACTIVE tokens (recent calls) whose data is stale,
  * a dead/failing onchain API degrades gracefully: calls stay unresolved, nothing crashes.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.engagement import models as em
from app.engagement.onchain import enrich_calls, refresh_tokens

SOL_CA = "9ybu4ArAY9iyGpjh99eYSmjn5tw3Jvyo9aeRFQqy6Ezh"


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _pair(address=SOL_CA, symbol="PENGU", chain="solana", liq=1_000_000.0,
          price="0.03425", mcap=342_581_320, change=5.4):
    return {
        "chainId": chain, "dexId": "raydium",
        "baseToken": {"address": address, "symbol": symbol, "name": symbol.title()},
        "priceUsd": price, "marketCap": mcap,
        "priceChange": {"h24": change},
        "liquidity": {"usd": liq},
        "info": {"imageUrl": f"https://img/{symbol}.png"},
    }


class FakeDex:
    """Stands in for the DexScreener client."""

    def __init__(self, by_contract=None, by_search=None, fail=False):
        self.by_contract = by_contract or {}
        self.by_search = by_search or {}
        self.fail = fail
        self.calls = []

    async def token_pairs(self, contract):
        self.calls.append(("token", contract))
        if self.fail:
            raise RuntimeError("dexscreener down")
        return self.by_contract.get(contract, [])

    async def search(self, query):
        self.calls.append(("search", query))
        if self.fail:
            raise RuntimeError("dexscreener down")
        return self.by_search.get(query, [])


def _call(tweet_id, *, ticker=None, contract=None, chain=None, ts=None):
    ts = ts or _utcnow()
    return em.EngCall(tweet_id=tweet_id, member_id="1001", ticker=ticker,
                      contract=contract, chain=chain,
                      confidence="contract" if contract else "ticker",
                      token_key=contract or f"${ticker}",
                      ts=ts, day=ts.date())


async def _seed(SessionLocal, rows):
    async with SessionLocal() as s:
        s.add_all(rows)
        await s.commit()


async def test_contract_call_resolves_and_upserts_token(eng_db):
    await _seed(eng_db, [_call("c1", contract=SOL_CA, chain="sol")])
    dex = FakeDex(by_contract={SOL_CA: [_pair()]})
    stats = await enrich_calls(dex=dex)
    assert stats["resolved"] == 1
    async with eng_db() as s:
        tok = (await s.execute(select(em.EngToken))).scalars().one()
        assert (tok.contract, tok.symbol, tok.chain) == (SOL_CA, "PENGU", "solana")
        assert tok.price_usd == pytest.approx(0.03425)
        c = (await s.execute(select(em.EngCall))).scalars().one()
        assert c.token_contract == SOL_CA
        assert c.price_at_call == pytest.approx(0.03425)   # fresh call -> snapshot


async def test_ticker_resolves_to_first_exact_symbol_above_floor(eng_db):
    """DexScreener's OWN result order is the trust signal — scam pools SPOOF liquidity
    (fake $1B+ pools beat real ones under a max-liquidity rule; seen live: LINK/UNI/AAVE
    resolved to counterfeits). First exact-symbol result above the floor wins."""
    await _seed(eng_db, [_call("c1", ticker="PENGU")])
    dex = FakeDex(by_search={"PENGU": [
        _pair(address="DUST111111111111111111111111111111111111111", symbol="PENGU", liq=5_000),  # under floor
        _pair(address=SOL_CA, symbol="PENGU", liq=44_000_000),        # first credible exact match
        _pair(address="SPOOF11111111111111111111111111111111111111", symbol="PENGU", liq=9e9),    # spoofed liq
        _pair(address="OTHER11111111111111111111111111111111111111", symbol="PENG", liq=99_000_000),
    ]})
    stats = await enrich_calls(dex=dex)
    assert stats["resolved"] == 1
    async with eng_db() as s:
        c = (await s.execute(select(em.EngCall))).scalars().one()
        assert c.token_contract == SOL_CA          # order beats (spoofable) liquidity


async def test_low_liquidity_ticker_stays_unresolved(eng_db):
    await _seed(eng_db, [_call("c1", ticker="RUGME")])
    dex = FakeDex(by_search={"RUGME": [_pair(symbol="RUGME", liq=900.0)]})  # under floor
    stats = await enrich_calls(dex=dex)
    assert stats["resolved"] == 0
    async with eng_db() as s:
        assert (await s.execute(select(em.EngCall))).scalars().one().token_contract is None


async def test_stale_call_gets_no_price_snapshot(eng_db):
    old = _utcnow() - timedelta(days=10)
    await _seed(eng_db, [_call("c1", contract=SOL_CA, chain="sol", ts=old)])
    dex = FakeDex(by_contract={SOL_CA: [_pair()]})
    await enrich_calls(dex=dex)
    async with eng_db() as s:
        c = (await s.execute(select(em.EngCall))).scalars().one()
        assert c.token_contract == SOL_CA           # resolved...
        assert c.price_at_call is None              # ...but no fake historical price


async def test_refresh_updates_only_stale_active_tokens(eng_db):
    fresh_ts = _utcnow()
    async with eng_db() as s:
        s.add(em.EngToken(contract=SOL_CA, chain="solana", symbol="PENGU",
                          price_usd=0.01, refreshed_at=_utcnow() - timedelta(hours=3)))
        s.add(em.EngToken(contract="COLD1111111111111111111111111111111111111111",
                          chain="solana", symbol="COLD",
                          price_usd=1.0, refreshed_at=_utcnow() - timedelta(hours=3)))
        active_call = _call("c1", contract=SOL_CA, chain="sol", ts=fresh_ts)
        active_call.token_contract = SOL_CA        # resolved call -> PENGU is "active"
        s.add(active_call)
        await s.commit()
    dex = FakeDex(by_contract={SOL_CA: [_pair(price="0.05")]})
    stats = await refresh_tokens(dex=dex)
    assert stats["refreshed"] == 1                  # only the active+stale one
    assert ("token", SOL_CA) in dex.calls
    async with eng_db() as s:
        tok = (await s.execute(select(em.EngToken)
                               .where(em.EngToken.contract == SOL_CA))).scalars().one()
        assert tok.price_usd == pytest.approx(0.05)


async def test_dead_onchain_api_degrades_gracefully(eng_db):
    await _seed(eng_db, [_call("c1", contract=SOL_CA, chain="sol")])
    stats = await enrich_calls(dex=FakeDex(fail=True))
    assert stats["resolved"] == 0                   # no crash, call stays unresolved


async def test_ticker_resolution_is_sticky(eng_db):
    """Once a ticker has resolved to a contract, later calls for the SAME ticker reuse it —
    resolution flapping split one token into duplicate leaderboard rows (user-reported)."""
    resolved = _call("c1", ticker="PENGU")
    resolved.token_contract = SOL_CA
    await _seed(eng_db, [resolved, _call("c2", ticker="PENGU")])
    dex = FakeDex(by_search={"PENGU": [
        _pair(address="Different1111111111111111111111111111111111", symbol="PENGU",
              liq=99_000_000),   # even a now-more-liquid pair must NOT win over the canon
    ]})
    stats = await enrich_calls(dex=dex)
    assert stats["resolved"] == 1
    async with eng_db() as s:
        c2 = (await s.execute(select(em.EngCall).where(em.EngCall.tweet_id == "c2"))).scalars().one()
        assert c2.token_contract == SOL_CA          # canon reused
    assert ("search", "PENGU") not in dex.calls     # no network flap


async def test_native_coin_tickers_never_resolve(eng_db):
    """$SOL/$ETH/$BTC are native coins — DEX 'pairs' for them are wrapped/fake copies
    (a counterfeit 'SOL' showed MC $2B on the board). Calls stay recorded but unresolved."""
    await _seed(eng_db, [_call("c1", ticker="SOL"), _call("c2", ticker="ETH")])
    dex = FakeDex(by_search={"SOL": [_pair(symbol="SOL", liq=5_000_000)],
                             "ETH": [_pair(symbol="ETH", liq=5_000_000)]})
    stats = await enrich_calls(dex=dex)
    assert stats["resolved"] == 0
    assert dex.calls == []                          # not even searched


async def test_stock_tickers_never_resolve(eng_db):
    """$MSTR/$NVDA/$TSLA are equities — every DEX 'match' is a copycat memecoin (live:
    MSTR @ $450k mcap, TSLA @ $14k). Recorded, never resolved via search."""
    await _seed(eng_db, [_call("c1", ticker="MSTR"), _call("c2", ticker="QQQ")])
    dex = FakeDex(by_search={"MSTR": [_pair(symbol="MSTR", liq=5_000_000)],
                             "QQQ": [_pair(symbol="QQQ", liq=5_000_000)]})
    stats = await enrich_calls(dex=dex)
    assert stats["resolved"] == 0
    assert dex.calls == []                          # not even searched


async def test_wrapped_native_contracts_never_resolve(eng_db):
    """Posting the wSOL/WETH/stable ADDRESS bypassed the ticker guard and put 'Wrapped SOL'
    on the calls board. The contract path applies the same policy: recorded, never resolved."""
    wsol = "So11111111111111111111111111111111111111112"
    await _seed(eng_db, [_call("c1", contract=wsol, chain="sol")])
    dex = FakeDex(by_contract={wsol: [_pair(address=wsol, symbol="SOL", liq=9e9)]})
    stats = await enrich_calls(dex=dex)
    assert stats["resolved"] == 0
    assert dex.calls == []                          # not even looked up


async def test_overlong_pool_address_cannot_poison_the_batch(eng_db):
    """A real 66-char pool hash overflowed varchar(64) and rolled back ENTIRE enrichment
    batches forever (newest-first -> same poison token every pass). Strings crossing the
    DexScreener boundary are clamped to column width; identifiers null rather than truncate."""
    long_pool = _pair()
    long_pool["pairAddress"] = "0x" + "ab" * 80                    # 162 chars -> nulled
    long_pool["baseToken"]["name"] = "N" * 400                     # label -> truncated
    await _seed(eng_db, [_call("c1", contract=SOL_CA, chain="sol")])
    stats = await enrich_calls(dex=FakeDex(by_contract={SOL_CA: [long_pool]}))
    assert stats["resolved"] == 1                   # the batch commits
    async with eng_db() as s:
        tok = (await s.execute(select(em.EngToken))).scalars().one()
        assert tok.pool_address is None             # corrupt-if-truncated -> nulled
        assert len(tok.name) == 128                 # harmless-if-truncated -> clamped


async def test_max_id_keyset_pages_past_unresolvable_clog(eng_db):
    """Native cashtags never resolve and stay in the newest-N window forever — without a
    cursor the sweep re-examines the same duds every pass and NEVER reaches older
    resolvable calls. max_id pages strictly downward past the clog."""
    old = _utcnow() - timedelta(days=2)
    await _seed(eng_db, [_call("gem", contract=SOL_CA, chain="sol", ts=old)])   # captured first
    await _seed(eng_db, [_call("dud1", ticker="BTC"), _call("dud2", ticker="ETH")])  # newest ids
    dex = FakeDex(by_contract={SOL_CA: [_pair()]})
    r1 = await enrich_calls(dex=dex, limit=2)      # window = the two duds
    assert (r1["resolved"], r1["examined"]) == (0, 2)
    r2 = await enrich_calls(dex=dex, limit=2)      # naive re-poll: same duds, still stuck
    assert r2["resolved"] == 0
    r3 = await enrich_calls(dex=dex, limit=2, max_id=r1["min_id"])   # keyset: past the clog
    assert r3["resolved"] == 1
    async with eng_db() as s:
        gem = (await s.execute(select(em.EngCall).where(em.EngCall.tweet_id == "gem"))).scalars().one()
        assert gem.token_contract == SOL_CA


async def test_spoofed_pair_numbers_are_sanity_clamped(eng_db):
    """Scam pools spoof marketCap/priceChange (seen live: +2.7e39%, $1.9T mcap).
    Absurd numbers must become NULL, never reach the UI."""
    await _seed(eng_db, [_call("c1", contract=SOL_CA, chain="sol")])
    bad = _pair(mcap=1.9e12, change=2.7e39, liq=15_000.0)   # mcap/liq ratio ~1.3e8 -> fake
    dex = FakeDex(by_contract={SOL_CA: [bad]})
    await enrich_calls(dex=dex)
    async with eng_db() as s:
        tok = (await s.execute(select(em.EngToken))).scalars().one()
        assert tok.mcap_usd is None                 # spoofed mcap nulled
        assert tok.change_24h is None               # absurd change nulled
        assert tok.price_usd is not None            # plausible fields kept
