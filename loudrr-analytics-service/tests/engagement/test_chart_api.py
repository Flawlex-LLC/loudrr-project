"""TDD spec — GET /v1/kol-calls/chart: price candles + KOL call points for one token.

The Bitget-style chart: price line over the window with each KOL call pinned at its time.
Candles come from GeckoTerminal (free, keyless) via the token's POOL address captured at
enrichment; responses are TTL-cached in-process and the endpoint degrades (never 500s).
"""
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.db.models import SmartSetMember
from app.engagement import models as em

SOL_CA = "9ybu4ArAY9iyGpjh99eYSmjn5tw3Jvyo9aeRFQqy6Ezh"
POOL = "PoolAddr111111111111111111111111111111111111"


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class FakeGecko:
    def __init__(self, candles=None, fail=False):
        self.candles = candles if candles is not None else []
        self.fail = fail
        self.calls = []

    async def ohlcv(self, network, pool, *, timeframe="hour", aggregate=4, limit=200):
        self.calls.append((network, pool, timeframe, aggregate))
        if self.fail:
            raise RuntimeError("gecko down")
        return self.candles


@pytest.fixture
async def client(eng_db):
    from app.main import app
    import app.engagement.api as eng_api
    eng_api._chart_cache.clear()  # isolate TTL cache between tests
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, eng_db


async def _seed(SessionLocal, ts):
    async with SessionLocal() as s:
        s.add(SmartSetMember(user_id="1", username="ansem", score=5.0))
        s.add(em.EngToken(contract=SOL_CA, chain="solana", symbol="PENGU",
                          name="Pengu", price_usd=0.034, refreshed_at=_utcnow(),
                          pool_address=POOL, network="solana"))
        s.add(em.EngCall(tweet_id="t1", member_id="1", ticker="PENGU", contract=SOL_CA,
                         chain="sol", confidence="contract", token_key=SOL_CA,
                         token_contract=SOL_CA, ts=ts, day=ts.date(),
                         price_at_call=0.02))
        await s.commit()


async def test_chart_returns_candles_and_call_points(client, monkeypatch):
    c, SessionLocal = client
    ts = _utcnow() - timedelta(hours=5)
    await _seed(SessionLocal, ts)

    now_s = int(datetime.now(timezone.utc).timestamp())
    fake = FakeGecko(candles=[[now_s - 7200, 0.01, 0.03, 0.009, 0.025],
                              [now_s - 3600, 0.025, 0.04, 0.02, 0.034]])
    import app.engagement.api as eng_api
    monkeypatch.setattr(eng_api, "_gecko", fake)

    r = await c.get("/v1/kol-calls/chart", params={"contract": SOL_CA})
    assert r.status_code == 200
    body = r.json()
    assert body["token"]["symbol"] == "PENGU"
    assert [p["price"] for p in body["candles"]] == [0.025, 0.034]   # closes
    assert body["calls"][0]["username"] == "ansem"
    assert body["calls"][0]["priceAtCall"] == pytest.approx(0.02)
    assert fake.calls and fake.calls[0][0] == "solana" and fake.calls[0][1] == POOL


async def test_chart_is_ttl_cached(client, monkeypatch):
    c, SessionLocal = client
    await _seed(SessionLocal, _utcnow())
    fake = FakeGecko(candles=[[1, 1, 1, 1, 1.0]])
    import app.engagement.api as eng_api
    monkeypatch.setattr(eng_api, "_gecko", fake)

    await c.get("/v1/kol-calls/chart", params={"contract": SOL_CA})
    await c.get("/v1/kol-calls/chart", params={"contract": SOL_CA})
    assert len(fake.calls) == 1          # second hit served from cache


async def test_chart_degrades_when_gecko_down(client, monkeypatch):
    c, SessionLocal = client
    await _seed(SessionLocal, _utcnow())
    import app.engagement.api as eng_api
    monkeypatch.setattr(eng_api, "_gecko", FakeGecko(fail=True))

    r = await c.get("/v1/kol-calls/chart", params={"contract": SOL_CA})
    assert r.status_code == 200
    body = r.json()
    assert body["candles"] == []         # no chart, but calls still usable
    assert body["calls"] and body["token"]["symbol"] == "PENGU"


async def test_chart_unknown_contract_is_empty_200(client):
    c, _ = client
    r = await c.get("/v1/kol-calls/chart", params={"contract": "nope"})
    assert r.status_code == 200
    assert r.json()["token"] is None
