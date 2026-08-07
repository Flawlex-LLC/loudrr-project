"""TDD spec — scripts/backfill_engagement.py (historical timeline backfill).

Invariants:
  * walks BACKWARD from the member's oldest fetched tweet via max_id: in the query,
  * stops at the cutoff; tweets beyond it that arrive in the same page are still stored
    (already paid for), and `reached` records how far back we truly got,
  * boundary leak (max_id is fuzzily inclusive) and squatter tweets are dropped,
  * NEVER touches eng_cursor (the forward watermark belongs to the daily worker),
  * ledger row per member -> resume skips done members; SUM(credits) is the crash-safe
    spend counter.
"""
from datetime import datetime, timedelta

import pytest

from app.engagement.models import EngBackfill, EngCursor, EngTweetRaw
from scripts.backfill_engagement import _spent_prior, _universe, backfill_member

NOW = datetime(2026, 7, 7, 12, 0, 0)
CUTOFF = NOW - timedelta(days=364)


def _t(tid: int, days_ago: int, author="m1"):
    dt = NOW - timedelta(days=days_ago)
    return {"id": str(tid), "createdAt": dt.strftime("%a %b %d %H:%M:%S +0000 %Y"),
            "text": f"tweet {tid}", "author": {"id": author}}


class FakeClient:
    """Serves a fixed descending timeline, honouring max_id: in the query."""

    def __init__(self, timeline):
        self.timeline = sorted(timeline, key=lambda t: -int(t["id"]))
        self.credits_spent = 0.0

    async def iter_search_tweets(self, *, query: str, since_id=None, max_pages: int = 5):
        max_id = None
        for tok in query.split():
            if tok.startswith("max_id:"):
                max_id = int(tok.split(":", 1)[1])
        page = [t for t in self.timeline if max_id is None or int(t["id"]) <= max_id]
        self.credits_spent += 15 * len(page)
        for t in page:
            yield t


async def _seed(SessionLocal, rows):
    async with SessionLocal() as s:
        s.add_all(rows)
        await s.commit()


async def test_walks_back_to_cutoff_and_marks_done(eng_db):
    await _seed(eng_db, [EngTweetRaw(tweet_id="5000", member_id="m1",
                                     created_at=NOW - timedelta(days=30), raw={})])
    client = FakeClient([
        _t(4000, 100), _t(3000, 200), _t(2000, 400),   # 2000 is beyond the cutoff
    ])
    r = await backfill_member(client, "m1", "alice", "5000",
                              NOW - timedelta(days=30), CUTOFF)
    assert r["reason"] == "cutoff"
    assert r["inserted"] == 3                      # beyond-cutoff tweet stored too (paid for)
    async with eng_db() as s:
        row = await s.get(EngBackfill, "m1")
        assert row.done is True
        assert row.tweets == 3
        assert row.credits > 0
        assert row.reached == NOW - timedelta(days=400)
        assert await s.get(EngCursor, "m1") is None  # forward watermark untouched


async def test_boundary_leak_and_squatter_dropped(eng_db):
    await _seed(eng_db, [EngTweetRaw(tweet_id="5000", member_id="m1",
                                     created_at=NOW - timedelta(days=30), raw={})])
    client = FakeClient([
        _t(5000, 30),                    # max_id boundary leak (== anchor) -> dropped
        _t(4000, 380),                   # real historical tweet, beyond cutoff -> stop after
        _t(3900, 385, author="squatter"),  # handle recycled -> dropped
    ])
    r = await backfill_member(client, "m1", "alice", "5000",
                              NOW - timedelta(days=30), CUTOFF)
    assert r["inserted"] == 1
    async with eng_db() as s:
        assert await s.get(EngTweetRaw, "4000") is not None
        assert await s.get(EngTweetRaw, "3900") is None


async def test_exhausted_timeline_marks_done(eng_db):
    await _seed(eng_db, [EngTweetRaw(tweet_id="5000", member_id="m1",
                                     created_at=NOW - timedelta(days=30), raw={})])
    r = await backfill_member(FakeClient([]), "m1", "alice", "5000",
                              NOW - timedelta(days=30), CUTOFF)
    assert r["reason"] == "exhausted"
    async with eng_db() as s:
        assert (await s.get(EngBackfill, "m1")).done is True


async def test_error_stays_not_done_for_retry(eng_db):
    class BoomClient:
        credits_spent = 0.0

        async def iter_search_tweets(self, **kw):
            raise RuntimeError("gateway 503")
            yield  # pragma: no cover

    await _seed(eng_db, [EngTweetRaw(tweet_id="5000", member_id="m1",
                                     created_at=NOW - timedelta(days=30), raw={})])
    r = await backfill_member(BoomClient(), "m1", "alice", "5000",
                              NOW - timedelta(days=30), CUTOFF)
    assert r["reason"] == "error"
    async with eng_db() as s:
        assert (await s.get(EngBackfill, "m1")).done is False   # retried next run


async def test_universe_selects_shallow_members_richest_first(eng_db):
    from app.db.models import SmartSetMember
    await _seed(eng_db, [
        SmartSetMember(user_id="deep", username="deep", score=9.0),
        SmartSetMember(user_id="shal", username="shal", score=5.0),
        SmartSetMember(user_id="rich", username="rich", score=8.0),
        SmartSetMember(user_id="done", username="done", score=7.0),
        # deep already reaches past the cutoff -> not in universe
        EngTweetRaw(tweet_id="100", member_id="deep",
                    created_at=CUTOFF - timedelta(days=5), raw={}),
        EngTweetRaw(tweet_id="200", member_id="shal",
                    created_at=NOW - timedelta(days=10), raw={}),
        EngTweetRaw(tweet_id="300", member_id="rich",
                    created_at=NOW - timedelta(days=20), raw={}),
        EngTweetRaw(tweet_id="400", member_id="done",
                    created_at=NOW - timedelta(days=10), raw={}),
        EngBackfill(member_id="done", done=True),      # ledger-resumed -> skipped
    ])
    uni = await _universe(CUTOFF)
    assert [m[0] for m in uni] == ["rich", "shal"]     # score order, deep+done excluded
    assert uni[0][2] == "300"                          # oldest tweet id carried along


async def test_spend_ledger_sums_prior_credits(eng_db):
    await _seed(eng_db, [
        EngBackfill(member_id="a", credits=150_000.0),
        EngBackfill(member_id="b", credits=50_000.0),
    ])
    assert await _spent_prior() == pytest.approx(2.0)  # $2 at 100k credits/USD
