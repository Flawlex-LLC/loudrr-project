"""TDD spec — the score→bucket→snapshot math, end to end on a synthetic DB.

Encodes the core invariants that make our numbers "mindshare" (and comparable to Kaito):
  * mindshare normalizes to Σ=1.0 per niche,
  * rank is dense 1..N by mindshare desc,
  * engagement × author-weight ordering is respected,
  * a token in two sectors is counted in both,
  * windows are rolling (an old tweet drops out of 24h but stays in 30d).
Runs on the throwaway temp DB (see tests/conftest.py).
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.mindshare import models as m
from app.mindshare.aggregate import rebuild_buckets, snapshot
from app.mindshare.score import score_vertical

UTC = timezone.utc


def _now():
    return datetime.now(UTC).replace(tzinfo=None)


async def _seed(SessionLocal, roster, tweets):
    async with SessionLocal() as s:
        s.add_all(roster)
        s.add_all(tweets)
        await s.commit()


def _kol(uid, weight=1.0, sector="ALL"):
    return m.MsRoster(vertical="crypto", sector=sector, role="kol", entity_id=uid, weight=weight)


def _token(eid, sector="ALL"):
    return m.MsRoster(vertical="crypto", sector=sector, role="token", entity_id=eid, symbol=eid)


def _tweet(tid, author, text, *, likes=0, rts=0, replies=0, quotes=0, views=0, ago_h=1):
    return m.MsTweetRaw(
        tweet_id=tid, author_id=author, created_at=_now() - timedelta(hours=ago_h),
        text=text, like_count=likes, retweet_count=rts, reply_count=replies,
        quote_count=quotes, view_count=views, is_retweet=False,
        raw={"text": text, "entities": {}})


@pytest.mark.asyncio
async def test_normalizes_to_one_and_orders_by_engagement(ms_db):
    await _seed(ms_db,
               [_kol("k1"), _kol("k2"), _token("BTC"), _token("SOL")],
               [_tweet("1", "k1", "$BTC moon", likes=100),
                _tweet("2", "k2", "$SOL szn", likes=50)])
    await score_vertical("crypto")
    await rebuild_buckets("crypto")
    await snapshot("crypto", now=_now())

    async with ms_db() as s:
        from sqlalchemy import select
        rows = (await s.execute(select(m.MsSnapshot).where(
            m.MsSnapshot.sector == "ALL", m.MsSnapshot.window == "7d")
            .order_by(m.MsSnapshot.rank))).scalars().all()
    ms = {r.entity_id: r.mindshare for r in rows}
    assert set(ms) == {"BTC", "SOL"}
    assert abs(sum(ms.values()) - 1.0) < 1e-9            # Σ = 1
    assert rows[0].entity_id == "BTC" and rows[0].rank == 1   # higher engagement ranks first
    assert [r.rank for r in rows] == [1, 2]              # dense ranks
    assert ms["BTC"] > ms["SOL"] > 0                     # more engagement -> more share (log-dampened)


@pytest.mark.asyncio
async def test_author_weight_changes_share(ms_db):
    # same engagement, but k1 has 3x the PageRank weight -> 3x the share
    await _seed(ms_db,
               [_kol("k1", weight=3.0), _kol("k2", weight=1.0), _token("BTC"), _token("SOL")],
               [_tweet("1", "k1", "$BTC", likes=10), _tweet("2", "k2", "$SOL", likes=10)])
    await score_vertical("crypto")
    await rebuild_buckets("crypto")
    await snapshot("crypto", now=_now())
    async with ms_db() as s:
        from sqlalchemy import select
        rows = (await s.execute(select(m.MsSnapshot).where(
            m.MsSnapshot.sector == "ALL", m.MsSnapshot.window == "7d"))).scalars().all()
    ms = {r.entity_id: r.mindshare for r in rows}
    assert abs(ms["BTC"] - 0.75) < 1e-6                 # 3 / (3+1)


@pytest.mark.asyncio
async def test_rolling_window_drops_old_tweets(ms_db):
    # BTC tweeted 200h ago (in 30d, NOT in 24h); SOL tweeted 1h ago (in both)
    await _seed(ms_db,
               [_kol("k1"), _token("BTC"), _token("SOL")],
               [_tweet("1", "k1", "$BTC", likes=100, ago_h=200),
                _tweet("2", "k1", "$SOL", likes=10, ago_h=1)])
    await score_vertical("crypto")
    await rebuild_buckets("crypto")
    await snapshot("crypto", now=_now())
    async with ms_db() as s:
        from sqlalchemy import select
        rows_24 = (await s.execute(select(m.MsSnapshot).where(
            m.MsSnapshot.sector == "ALL", m.MsSnapshot.window == "24h"))).scalars().all()
        rows_30 = (await s.execute(select(m.MsSnapshot).where(
            m.MsSnapshot.sector == "ALL", m.MsSnapshot.window == "30d"))).scalars().all()
    e24 = {r.entity_id for r in rows_24}
    e30 = {r.entity_id for r in rows_30}
    assert "BTC" not in e24 and "SOL" in e24            # old BTC dropped from 24h
    assert {"BTC", "SOL"} <= e30                          # both present in 30d
