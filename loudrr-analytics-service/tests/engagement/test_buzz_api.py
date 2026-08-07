"""TDD spec — GET /v1/kol-calls/buzz (X-activity Social Buzz for a token).

Invariants:
  * buzz is score-WEIGHTED: a Megaphone's call moves the needle more than a Whisper's,
  * one signal per (tweet, caller) — a tweet with ticker+contract is ONE signal not two,
  * signal leaderboard ranks by weighted signal count, carries public score + tier + followers,
  * NEVER 500s: unknown contract / internal failure -> 200 coverage:"none".
"""
from datetime import datetime, timedelta

import httpx
import pytest

from app.db.models import RankedAccount
from app.engagement import models as em

C = "So1TokenContract1111111111111111111111111111"
NOW = datetime.utcnow()


def _call(tweet_id, member_id, days_ago, ticker="AAA"):
    ts = NOW - timedelta(days=days_ago)
    return em.EngCall(tweet_id=tweet_id, member_id=member_id, ticker=ticker,
                      token_key=f"${ticker}", confidence="ticker", token_contract=C,
                      ts=ts, day=ts.date())


def _ranked(uid, uname, score, followers=1000):
    return RankedAccount(user_id=uid, rank=1, username=uname, display_username=uname,
                         name=uname, followers=followers, following=10, verified=True,
                         elite_followers=5, raw_score=score, score=score, categories="")


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


async def test_buzz_is_influence_weighted(client):
    c, SessionLocal = client
    await _seed(SessionLocal, [
        _ranked("mega", "bigkol", 5200),      # Megaphone
        _ranked("whis", "smallkol", 700),     # Whisper
        _call("t1", "mega", 1),
        _call("t2", "whis", 1),
    ])
    r = await c.get("/v1/kol-calls/buzz", params={"contract": C, "window": "7d"})
    assert r.status_code == 200
    body = r.json()
    assert body["coverage"] == "tracked"
    sig = {s["username"]: s for s in body["signals"]}
    assert sig["bigkol"]["tier"] == "Megaphone"
    assert sig["smallkol"]["tier"] == "Whisper"
    # bigkol ranks above smallkol despite equal raw counts (weighted by score)
    assert body["signals"][0]["username"] == "bigkol"


async def test_dedup_one_signal_per_tweet_and_caller(client):
    c, SessionLocal = client
    await _seed(SessionLocal, [
        _ranked("k", "kol", 3000),
        _call("dup", "k", 1, ticker="AAA"),
        _call("dup", "k", 1, ticker="AAA2"),   # same tweet, 2nd eng_call row -> ONE signal
    ])
    r = await c.get("/v1/kol-calls/buzz", params={"contract": C})
    body = r.json()
    assert body["signals"][0]["signals"] == 1


async def test_buzz_index_and_series_normalized(client):
    c, SessionLocal = client
    await _seed(SessionLocal, [
        _ranked("k", "kol", 3000),
        _call("t1", "k", 0),   # today -> last bucket
    ])
    r = await c.get("/v1/kol-calls/buzz", params={"contract": C, "window": "7d"})
    body = r.json()
    assert 0 <= body["buzzIndex"] <= 100
    assert len(body["series"]) == 28          # 7d / 6h buckets
    assert max(p["buzz"] for p in body["series"]) == 100.0   # peak normalized to 100


async def test_unknown_contract_returns_200_none(client):
    c, _ = client
    r = await c.get("/v1/kol-calls/buzz", params={"contract": "nope"})
    assert r.status_code == 200
    assert r.json()["coverage"] == "none"


async def test_missing_contract_returns_200_none(client):
    c, _ = client
    r = await c.get("/v1/kol-calls/buzz")
    assert r.status_code == 200
    assert r.json()["coverage"] == "none"


async def test_internal_failure_returns_200_none(client, monkeypatch):
    c, _ = client
    import app.engagement.api as eng_api

    class _Boom:
        def __call__(self):
            raise RuntimeError("db down")

    monkeypatch.setattr(eng_api, "SessionLocal", _Boom())
    r = await c.get("/v1/kol-calls/buzz", params={"contract": C})
    assert r.status_code == 200
    assert r.json()["coverage"] == "none"
