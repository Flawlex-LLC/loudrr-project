"""TDD spec — outbound-edge extraction from raw timeline tweets.

The engagement system's core invariant: from a smart-set member's OWN timeline we derive
"member engaged target on day D" edges (reply / retweet / quote), with:
  * target identity taken from the payload itself (inReplyToUserId / nested author) — no lookups,
  * self-engagement dropped (accounts threading themselves must not inflate their own heatmap),
  * usernames normalized to lowercase (the API queries by userName),
  * idempotent persistence (re-running extraction never duplicates an edge).
"""
from datetime import date

import pytest
from sqlalchemy import select

from app.engagement import models as em
from app.engagement.extract import edges_from_tweet, extract_edges

MEMBER = "1001"  # the polled smart-set member (timeline owner / engager)

# "Mon Jun 29 06:21:12 +0000 2026" — Twitter's createdAt format (matches live payloads)
CREATED = "Mon Jun 29 06:21:12 +0000 2026"


def _reply(tid="t1", target_id="2002", target_name="TargetGuy", created=CREATED):
    return {
        "id": tid, "createdAt": created, "text": "@TargetGuy gm",
        "author": {"id": MEMBER, "userName": "member"},
        "isReply": True, "inReplyToId": "orig1",
        "inReplyToUserId": target_id, "inReplyToUsername": target_name,
    }


def _retweet(tid="t2", target_id="3003", target_name="RtTarget", nested_key="retweeted_tweet"):
    return {
        "id": tid, "createdAt": CREATED, "text": "RT @RtTarget: alpha",
        "author": {"id": MEMBER, "userName": "member"}, "isRetweet": True,
        nested_key: {"id": "orig2", "author": {"id": target_id, "userName": target_name}},
    }


def _quote(tid="t3", target_id="4004", target_name="QtTarget", nested_key="quoted_tweet"):
    return {
        "id": tid, "createdAt": CREATED, "text": "this. so much this",
        "author": {"id": MEMBER, "userName": "member"}, "isQuote": True,
        nested_key: {"id": "orig3", "author": {"id": target_id, "userName": target_name}},
    }


# ── pure extraction ─────────────────────────────────────────────────────────

def test_reply_yields_edge():
    edges = edges_from_tweet(_reply(), MEMBER)
    assert len(edges) == 1
    e = edges[0]
    assert e["kind"] == "reply"
    assert e["engager_id"] == MEMBER
    assert e["target_id"] == "2002"
    assert e["target_username"] == "targetguy"       # lowercased
    assert e["tweet_id"] == "t1"
    assert e["day"] == date(2026, 6, 29)             # from createdAt, UTC


def test_self_reply_dropped():
    assert edges_from_tweet(_reply(target_id=MEMBER, target_name="member"), MEMBER) == []


def test_retweet_yields_edge_both_key_spellings():
    for key in ("retweeted_tweet", "retweetedTweet"):
        edges = edges_from_tweet(_retweet(nested_key=key), MEMBER)
        assert len(edges) == 1, key
        assert edges[0]["kind"] == "retweet"
        assert edges[0]["target_id"] == "3003"
        assert edges[0]["target_username"] == "rttarget"


def test_quote_yields_edge_both_key_spellings():
    for key in ("quoted_tweet", "quotedTweet"):
        edges = edges_from_tweet(_quote(nested_key=key), MEMBER)
        assert len(edges) == 1, key
        assert edges[0]["kind"] == "quote"
        assert edges[0]["target_id"] == "4004"


def test_self_retweet_and_self_quote_dropped():
    assert edges_from_tweet(_retweet(target_id=MEMBER), MEMBER) == []
    assert edges_from_tweet(_quote(target_id=MEMBER), MEMBER) == []


def test_reply_that_also_quotes_yields_two_edges():
    t = _reply()
    t["quoted_tweet"] = {"id": "orig9", "author": {"id": "5005", "userName": "Quoted"}}
    edges = edges_from_tweet(t, MEMBER)
    assert {(e["kind"], e["target_id"]) for e in edges} == {("reply", "2002"), ("quote", "5005")}


def test_original_tweet_yields_nothing():
    t = {"id": "t9", "createdAt": CREATED, "text": "just alpha",
         "author": {"id": MEMBER, "userName": "member"}}
    assert edges_from_tweet(t, MEMBER) == []


def test_malformed_payloads_do_not_crash():
    assert edges_from_tweet({}, MEMBER) == []                              # empty
    assert edges_from_tweet({"id": "x"}, MEMBER) == []                     # bare id
    assert edges_from_tweet({"id": "x", "isReply": True}, MEMBER) == []    # reply w/o target
    assert edges_from_tweet({"id": "x", "retweeted_tweet": {}}, MEMBER) == []
    assert edges_from_tweet({"id": "x", "retweeted_tweet": {"author": {}}}, MEMBER) == []
    assert edges_from_tweet({"id": "x", "createdAt": "not-a-date", "isReply": True,
                             "inReplyToUserId": "7", "inReplyToUsername": "z"}, MEMBER) != []


def test_foreign_authored_tweet_not_attributed_to_member():
    # defensive: if the payload's author differs from the polled member, don't fabricate an edge
    t = _reply()
    t["author"] = {"id": "9999", "userName": "someoneelse"}
    assert edges_from_tweet(t, MEMBER) == []


# ── persistence (idempotency) ───────────────────────────────────────────────

async def _seed_raw(SessionLocal, tweets):
    async with SessionLocal() as s:
        for t in tweets:
            s.add(em.EngTweetRaw(tweet_id=str(t["id"]), member_id=MEMBER,
                                 created_at=None, raw=t))
        await s.commit()


async def test_extract_edges_persists_and_marks_parsed(eng_db):
    await _seed_raw(eng_db, [_reply(), _retweet(), _quote(),
                             {"id": "t9", "createdAt": CREATED,
                              "author": {"id": MEMBER, "userName": "member"}, "text": "original"}])
    stats = await extract_edges()
    assert stats["edges_inserted"] == 3
    async with eng_db() as s:
        edges = (await s.execute(select(em.EngEdge))).scalars().all()
        assert len(edges) == 3
        unparsed = (await s.execute(
            select(em.EngTweetRaw).where(em.EngTweetRaw.parsed.is_(False)))).scalars().all()
        assert unparsed == []          # everything (incl. the edgeless original) marked parsed


async def test_extract_edges_is_idempotent(eng_db):
    await _seed_raw(eng_db, [_reply(), _retweet()])
    first = await extract_edges()
    assert first["edges_inserted"] == 2

    # simulate a re-ingest of the SAME tweets (parsed reset, e.g. full replay)
    async with eng_db() as s:
        for r in (await s.execute(select(em.EngTweetRaw))).scalars().all():
            r.parsed = False
            await s.merge(r)
        await s.commit()

    second = await extract_edges()
    assert second["edges_inserted"] == 0           # unique(tweet_id, target_id) holds
    async with eng_db() as s:
        assert len((await s.execute(select(em.EngEdge))).scalars().all()) == 2
