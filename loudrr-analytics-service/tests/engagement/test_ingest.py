"""TDD spec — smart-set OUTBOUND ingest via the user timeline (dedicated, budget-capped, incremental).

Source: ``/user/last_tweets`` (the user-timeline endpoint) with includeReplies, queried by NUMERIC
user_id — captures the complete outbound stream (replies + native RTs + quotes + originals). We
formerly used advanced_search while the gateway's last_tweets was broken; it was fixed 2026-07-23
(deep pagination + reply/RT/quote parity, identical field names). Invariants:
  * one timeline pull per member, queried by user_id (not handle -> immune to squat/rename),
  * stores the FULL raw tweet JSON (bronze; later powers mindshare + KOL calls),
  * advances a per-member since_id cursor and passes it on the next run (incremental,
    replayable; time operators are fuzzy so incrementality is tweet-id based),
  * members without a username are skipped and counted (unchanged for now),
  * re-ingesting the same tweets never duplicates rows,
  * a budget ceiling stops SCHEDULING new members once spend crosses it (the analytics crawl's
    wallet can't be drained by this system),
  * one bad member doesn't kill the run.
"""
from sqlalchemy import select

from app.db.models import SmartSetMember
from app.engagement import models as em
from app.engagement.ingest import ingest_members
from app.engagement.service import run_once

CREATED = "Mon Jun 29 06:21:12 +0000 2026"


def _member(uid, username=None, seed=True, score=1.0):
    return SmartSetMember(user_id=uid, username=username or f"user{uid}",
                          is_seed=seed, score=score)


def _tweet(tid, author_id, **extra):
    return {"id": tid, "createdAt": CREATED, "text": "gm",
            "author": {"id": author_id, "userName": f"user{author_id}"}, **extra}


class FakeClient:
    """Stands in for TwitterAPIClient. Implements BOTH ingest sources (advanced_search, the default,
    and the timeline path) and records each call as the member id so assertions are source-agnostic.
    Canned data is keyed by the _member naming convention (id "100" -> "user100")."""

    def __init__(self, timelines, usd_per_member=0.0):
        self.timelines = timelines            # "user{id}" -> list[tweet dict]
        self.usd_per_member = usd_per_member
        self.calls = []                       # (member_id, since_id) — same for both sources
        self.credits_spent = 0.0
        self._usd = 0.0

    @property
    def usd_spent(self):
        return self._usd

    def _emit(self, key, since_id):
        for t in self.timelines.get(key, []):
            tid = str(t["id"])
            if since_id and tid.isdigit() and int(tid) <= int(since_id):
                return
            yield t

    async def iter_search_tweets(self, *, query, since_id=None, max_pages=5):
        uname = query.split()[0].split(":", 1)[1]     # "from:user100 ..." -> "user100"
        self.calls.append((uname[4:] if uname.startswith("user") else uname, since_id))  # -> "100"
        self._usd += self.usd_per_member
        for t in self._emit(uname, since_id):
            yield t

    async def iter_user_tweets(self, *, user_id=None, username=None, since_id=None,
                               max_pages=5, include_replies=False):
        self.calls.append((str(user_id), since_id))
        self._usd += self.usd_per_member
        for t in self._emit(f"user{user_id}", since_id):
            yield t


async def _seed_members(SessionLocal, members):
    async with SessionLocal() as s:
        s.add_all(members)
        await s.commit()


async def test_ingest_stores_raw_and_advances_cursor(eng_db):
    await _seed_members(eng_db, [_member("100"), _member("200")])
    fake = FakeClient({"user100": [_tweet("11", "100"), _tweet("10", "100")],
                       "user200": [_tweet("21", "200")]})
    stats = await ingest_members(client=fake)
    assert stats["tweets_inserted"] == 3
    assert stats["accounts"] == 2

    # one timeline pull per member, queried by NUMERIC user_id
    assert sorted(uid for uid, _ in fake.calls) == ["100", "200"]

    async with eng_db() as s:
        raws = (await s.execute(select(em.EngTweetRaw))).scalars().all()
        assert {r.tweet_id for r in raws} == {"11", "10", "21"}
        assert all(r.raw["id"] in {"11", "10", "21"} for r in raws)   # full raw retained
        cur = {c.member_id: c for c in (await s.execute(select(em.EngCursor))).scalars().all()}
        assert cur["100"].since_id == "11"
        assert cur["200"].since_id == "21"


async def test_second_run_is_incremental_and_deduped(eng_db):
    await _seed_members(eng_db, [_member("100")])
    fake = FakeClient({"user100": [_tweet("11", "100")]})
    await ingest_members(client=fake)

    fake2 = FakeClient({"user100": [_tweet("12", "100"), _tweet("11", "100")]})
    stats = await ingest_members(client=fake2)
    # cursor from run 1 passed as since_id
    assert fake2.calls == [("100", "11")]
    assert stats["tweets_inserted"] == 1          # only the new tweet lands
    async with eng_db() as s:
        assert {r.tweet_id for r in (await s.execute(select(em.EngTweetRaw))).scalars().all()} \
            == {"11", "12"}
        cur = (await s.execute(select(em.EngCursor))).scalars().one()
        assert cur.since_id == "12"


async def test_member_without_username_is_skipped(eng_db):
    await _seed_members(eng_db, [
        _member("100"),
        SmartSetMember(user_id="300", username=None, is_seed=True, score=1.0),
    ])
    fake = FakeClient({"user100": []})
    stats = await ingest_members(client=fake)
    assert stats["accounts"] == 2
    assert stats["skipped_no_username"] == 1
    assert len(fake.calls) == 1


async def test_non_seed_members_excluded_by_default_universe(eng_db):
    await _seed_members(eng_db, [_member("100", seed=True), _member("999", seed=False)])
    fake = FakeClient({"user100": [], "user999": []})
    stats = await ingest_members(client=fake)
    assert stats["accounts"] == 1
    assert [uid for uid, _ in fake.calls] == ["100"]


async def test_top_n_universe_is_seeds_union_topn(eng_db, monkeypatch):
    """top:N = top-N by PageRank UNION all seeds (a low-scored seed still gets polled)."""
    from app.core.config import settings

    await _seed_members(eng_db, [
        _member("1", seed=False, score=9.0),   # top by score
        _member("2", seed=False, score=8.0),   # top by score
        _member("3", seed=False, score=7.0),   # outside top:2 and not a seed -> excluded
        _member("4", seed=True, score=0.1),    # low-scored seed -> still included
    ])
    monkeypatch.setattr(settings, "engagement_universe", "top:2")
    fake = FakeClient({f"user{m}": [] for m in ("1", "2", "3", "4")})
    stats = await ingest_members(client=fake)
    assert stats["accounts"] == 3
    assert {uid for uid, _ in fake.calls} == {"1", "2", "4"}


async def test_budget_ceiling_stops_scheduling(eng_db):
    await _seed_members(eng_db, [_member(str(i), score=10 - i) for i in range(10)])
    fake = FakeClient({f"user{i}": [] for i in range(10)}, usd_per_member=1.0)
    stats = await ingest_members(client=fake, budget_usd=3.0, concurrency=1)
    # spend hits the ceiling after ~3 members -> the rest are never scheduled
    assert len(fake.calls) < 10
    assert stats["budget_stopped"] is True


async def test_one_bad_member_does_not_kill_run(eng_db):
    await _seed_members(eng_db, [_member("100"), _member("200")])

    class Exploding(FakeClient):
        async def iter_search_tweets(self, *, query, **kw):
            if "user100" in query:
                raise RuntimeError("boom")
            async for t in super().iter_search_tweets(query=query, **kw):
                yield t

    fake = Exploding({"user200": [_tweet("21", "200")]})
    stats = await ingest_members(client=fake)
    assert stats["tweets_inserted"] == 1          # member 200 still landed


async def test_partial_fetch_does_not_advance_cursor(eng_db):
    """A mid-pagination failure must NOT advance since_id — advancing past unfetched
    territory silently loses every tweet in the gap forever (review finding #1)."""
    await _seed_members(eng_db, [_member("100")])
    await ingest_members(client=FakeClient({"user100": [_tweet("50", "100")]}))  # cursor -> 50

    class ExplodesMidway(FakeClient):
        async def iter_search_tweets(self, *, query, since_id=None, **kw):
            uname = query.split()[0].split(":", 1)[1]
            self.calls.append((uname[4:] if uname.startswith("user") else uname, since_id))
            yield _tweet("90", "100")             # newest lands...
            raise RuntimeError("page 2 died")     # ...then pagination fails

    fake = ExplodesMidway({})
    stats = await ingest_members(client=fake)
    assert stats["tweets_inserted"] == 1          # the fetched row is kept (dedup absorbs)
    async with eng_db() as s:
        cur = (await s.execute(select(em.EngCursor))).scalars().one()
        assert cur.since_id == "50"               # cursor did NOT jump to 90


async def test_squatted_handle_tweets_never_stored(eng_db):
    """includeReplies pads the timeline with thread-context tweets authored by a DIFFERENT user
    id — those must not pollute bronze or advance the member's cursor (review finding #3)."""
    await _seed_members(eng_db, [_member("100")])
    squatter_tweet = _tweet("99", "31337")        # author.id != member id
    fake = FakeClient({"user100": [squatter_tweet, _tweet("11", "100")]})
    stats = await ingest_members(client=fake)
    assert stats["tweets_inserted"] == 1
    async with eng_db() as s:
        raws = (await s.execute(select(em.EngTweetRaw))).scalars().all()
        assert [r.tweet_id for r in raws] == ["11"]
        cur = (await s.execute(select(em.EngCursor))).scalars().one()
        assert cur.since_id == "11"               # not 99


async def test_timeline_source_queries_by_user_id(eng_db, monkeypatch):
    """engagement_source='timeline' pulls the user timeline by NUMERIC id (iter_user_tweets),
    never advanced_search — same edges, rename-immune. Default stays advanced_search."""
    from app.core.config import settings
    monkeypatch.setattr(settings, "engagement_source", "timeline")
    await _seed_members(eng_db, [_member("100")])

    class TimelineOnly(FakeClient):
        async def iter_search_tweets(self, *, query, **kw):
            raise AssertionError("timeline source must not call advanced_search")
            yield  # noqa: marks this an async generator

    fake = TimelineOnly({"user100": [_tweet("11", "100"), _tweet("10", "100")]})
    stats = await ingest_members(client=fake)
    assert stats["tweets_inserted"] == 2
    assert fake.calls == [("100", None)]          # queried by id (timeline), not a from: query


async def test_run_once_end_to_end_builds_edges(eng_db):
    """Integration: ingest (fake client) -> extract -> queryable edges."""
    await _seed_members(eng_db, [_member("100")])
    reply = _tweet("11", "100", isReply=True, inReplyToUserId="7007",
                   inReplyToUsername="ViewedGuy")
    fake = FakeClient({"user100": [reply, _tweet("10", "100")]})
    stats = await run_once(client=fake)
    assert stats["ingest"]["tweets_inserted"] == 2
    assert stats["extract"]["edges_inserted"] == 1
    async with eng_db() as s:
        e = (await s.execute(select(em.EngEdge))).scalars().one()
        assert (e.engager_id, e.target_id, e.target_username, e.kind) \
            == ("100", "7007", "viewedguy", "reply")
