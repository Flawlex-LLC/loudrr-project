"""TDD spec — GET /v1/kol-calls serving contract (the Bitget-style KOL-signals screen).

Invariants:
  * leaderboard groups by RESOLVED token: calls count, distinct KOLs, joined market data,
  * unresolved calls never reach the UI (garbage contracts can't leak),
  * window filter (24h / 7d / 30d) bounds by call ts,
  * kolSample carries usernames for the avatar row,
  * token detail lists the individual calls (who, when, price-at-call, tweet id),
  * NEVER 500s — failure degrades to an empty 200 (same funnel-safety bar as
    /smart-engagement).
"""
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.db.models import SmartSetMember
from app.engagement import models as em

SOL_CA = "9ybu4ArAY9iyGpjh99eYSmjn5tw3Jvyo9aeRFQqy6Ezh"
CA2 = "6ogzHhzdrQr9Pgv6hZ2MNze7UrzBMAFyBBWUYp1Fhitx"     # a second ordinary token (RAY-like)
WSOL = "So11111111111111111111111111111111111111112"     # wrapped-native: must NEVER surface


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _call(tweet_id, member, contract=SOL_CA, hours_ago=1.0, price_at_call=None):
    ts = _utcnow() - timedelta(hours=hours_ago)
    return em.EngCall(tweet_id=tweet_id, member_id=member, ticker="PENGU",
                      contract=contract, chain="sol", confidence="contract",
                      token_key=contract, token_contract=contract, ts=ts, day=ts.date(),
                      price_at_call=price_at_call)


def _token(contract=SOL_CA, symbol="PENGU", price=0.034, mcap=342_000_000.0, change=5.4):
    return em.EngToken(contract=contract, chain="solana", symbol=symbol,
                       name=symbol.title(), image=f"https://img/{symbol}.png",
                       price_usd=price, mcap_usd=mcap, change_24h=change,
                       liquidity_usd=44_000_000.0, refreshed_at=_utcnow())


@pytest.fixture
async def client(eng_db):
    from app.main import app
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, eng_db


async def _seed(SessionLocal, rows):
    async with SessionLocal() as s:
        s.add_all(rows)
        await s.commit()


async def test_leaderboard_groups_counts_and_joins_market_data(client):
    c, SessionLocal = client
    await _seed(SessionLocal, [
        SmartSetMember(user_id="1", username="ansem", score=5.0),
        SmartSetMember(user_id="2", username="cobie", score=4.0),
        _token(), _token(CA2, symbol="RAY", mcap=2_000_000.0),
        _token(WSOL, symbol="SOL", mcap=80e9),
        _call("t1", "1"), _call("t2", "1"), _call("t3", "2"),   # PENGU: 3 calls, 2 KOLs
        _call("t4", "2", contract=CA2),                          # RAY  : 1 call, 1 KOL
        _call("t5", "1", contract=WSOL), _call("t6", "2", contract=WSOL),  # noise
    ])
    r = await c.get("/v1/kol-calls", params={"window": "24h"})
    assert r.status_code == 200
    body = r.json()
    assert body["window"] == "24h"
    top = body["items"][0]
    assert (top["symbol"], top["calls"], top["kols"]) == ("PENGU", 3, 2)
    assert top["contract"] == SOL_CA
    assert top["mcapUsd"] == pytest.approx(342_000_000.0)
    assert top["change24h"] == pytest.approx(5.4)
    assert {k["username"] for k in top["kolSample"]} == {"ansem", "cobie"}
    assert body["items"][1]["symbol"] == "RAY"
    # wrapped-native contract calls are quote-currency noise — never on the board,
    # even though they out-call everything ("SOL shown twice" bug)
    assert all(i["contract"] != WSOL for i in body["items"])


async def test_window_filters_old_calls(client):
    c, SessionLocal = client
    await _seed(SessionLocal, [
        SmartSetMember(user_id="1", username="ansem", score=5.0),
        _token(),
        _call("t1", "1", hours_ago=1),
        _call("t2", "1", hours_ago=30),          # outside 24h
    ])
    r = await c.get("/v1/kol-calls", params={"window": "24h"})
    assert r.json()["items"][0]["calls"] == 1
    r7 = await c.get("/v1/kol-calls", params={"window": "7d"})
    assert r7.json()["items"][0]["calls"] == 2


async def test_unresolved_calls_never_reach_the_ui(client):
    c, SessionLocal = client
    async with SessionLocal() as s:
        s.add(SmartSetMember(user_id="1", username="ansem", score=5.0))
        ts = _utcnow()
        # unresolved: no token_contract, no eng_token row
        s.add(em.EngCall(tweet_id="t9", member_id="1", ticker="RUGME", contract=None,
                         chain=None, confidence="ticker", token_key="$RUGME",
                         ts=ts, day=ts.date()))
        await s.commit()
    r = await c.get("/v1/kol-calls")
    assert r.status_code == 200
    assert r.json()["items"] == []


async def test_token_detail_lists_individual_calls(client):
    c, SessionLocal = client
    await _seed(SessionLocal, [
        SmartSetMember(user_id="1", username="ansem", score=5.0),
        _token(),
        _call("t1", "1", price_at_call=0.02),
    ])
    r = await c.get("/v1/kol-calls/token", params={"contract": SOL_CA})
    assert r.status_code == 200
    body = r.json()
    assert body["token"]["symbol"] == "PENGU"
    assert len(body["calls"]) == 1
    call = body["calls"][0]
    assert call["username"] == "ansem"
    assert call["tweetId"] == "t1"
    assert call["priceAtCall"] == pytest.approx(0.02)


async def test_ticker_plus_contract_tweet_counts_as_ONE_call(client):
    """One tweet carrying both $TICKER and the token's contract = two eng_call rows but
    ONE call on the leaderboard, and ONE entry in the token detail (review finding #6)."""
    c, SessionLocal = client
    ts = _utcnow() - timedelta(hours=1)
    async with SessionLocal() as s:
        s.add(SmartSetMember(user_id="1", username="ansem", score=5.0))
        s.add(_token())
        s.add(em.EngCall(tweet_id="t1", member_id="1", ticker="PENGU", contract=None,
                         chain=None, confidence="ticker", token_key="$PENGU",
                         token_contract=SOL_CA, ts=ts, day=ts.date()))
        s.add(em.EngCall(tweet_id="t1", member_id="1", ticker=None, contract=SOL_CA,
                         chain="sol", confidence="contract", token_key=SOL_CA,
                         token_contract=SOL_CA, ts=ts, day=ts.date()))
        await s.commit()
    r = await c.get("/v1/kol-calls", params={"window": "24h"})
    assert r.json()["items"][0]["calls"] == 1
    r2 = await c.get("/v1/kol-calls/token", params={"contract": SOL_CA})
    assert len(r2.json()["calls"]) == 1


async def test_internal_failure_returns_200_empty(client, monkeypatch):
    c, _ = client
    import app.engagement.api as eng_api

    class _Boom:
        def __call__(self):
            raise RuntimeError("db down")

    monkeypatch.setattr(eng_api, "SessionLocal", _Boom())
    r = await c.get("/v1/kol-calls")
    assert r.status_code == 200
    assert r.json()["items"] == []


class _FakeGecko:
    """Minimal GeckoTerminal stub: one KOL-wallet buy + one anonymous buy in the pool."""

    async def trades(self, network, pool):
        return [
            {"tx_from_address": "KOLWALLET1111", "kind": "buy", "volume_in_usd": "8210",
             "price_to_in_usd": "0.34", "block_timestamp": "2026-07-07T05:30:00Z"},
            {"tx_from_address": "0xANON99", "kind": "buy", "volume_in_usd": "15.12",
             "price_to_in_usd": "0.34", "block_timestamp": "2026-07-07T05:31:00Z"},
        ]


async def test_tape_labels_kol_wallets_with_score(client, monkeypatch):
    """The live tape labels a vault wallet with its handle + Loudrr score; anonymous buyers
    show an abbreviated address and no score. Both still appear (it's a full trade tape)."""
    c, SessionLocal = client
    import app.engagement.api as eng_api

    eng_api._tape_cache.clear()
    monkeypatch.setattr(eng_api, "_gecko", _FakeGecko())
    async with SessionLocal() as s:
        s.add(_token())
        s.add(em.EngToken(contract=CA2, chain="solana", symbol="X", name="X",
                          pool_address="POOLX", network="solana", refreshed_at=_utcnow()))
        s.add(em.EngWallet(handle="meechie", address="KOLWALLET1111", chain="sol",
                           source="kolscan", confidence="leaderboard", score=2350.0,
                           smart_followers=41))
        await s.commit()

    r = await c.get("/v1/kol-calls/tape", params={"contract": CA2})
    assert r.status_code == 200
    trades = r.json()["trades"]
    assert len(trades) == 2
    kol = next(t for t in trades if t["isKol"])
    anon = next(t for t in trades if not t["isKol"])
    assert kol["handle"] == "meechie" and kol["score"] == 2350.0 and kol["tier"] == "Signal"
    assert anon["handle"] is None and anon["score"] is None
    assert anon["wallet"].startswith("0xAN")


async def test_tape_unknown_token_returns_empty(client):
    c, _ = client
    r = await c.get("/v1/kol-calls/tape", params={"contract": "nope"})
    assert r.status_code == 200
    assert r.json()["trades"] == []
