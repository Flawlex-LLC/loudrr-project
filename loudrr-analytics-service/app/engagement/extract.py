"""Silver extraction: raw member tweets -> outbound engagement edges.

``edges_from_tweet`` is pure (unit-testable, replayable); ``extract_edges`` drives it over
unparsed bronze rows in batches, idempotently (unique(tweet_id, target_id) + pre-filter).

Edge semantics (validated against live gateway payloads 2026-07-02):
  * reply   -> target = ``inReplyToUserId`` / ``inReplyToUsername``
  * retweet -> target = ``retweeted_tweet.author``   (wrapper tweet's createdAt = RT action time)
  * quote   -> target = ``quoted_tweet.author``
  * self-engagement dropped (accounts thread themselves constantly — validated as the majority
    of reply items in the wild; keeping them would inflate a member's own heatmap)
  * likes are structurally uncapturable (a like never appears in the liker's timeline).
"""
from __future__ import annotations

import logging

from sqlalchemy import select

from app.core.util import parse_twitter_dt as _parse_dt
from app.db.session import Base, SessionLocal, engine
from app.engagement.models import EngEdge, EngTweetRaw

logger = logging.getLogger("eng.extract")


def edges_from_tweet(t: dict, member_id: str) -> list[dict]:
    """All outbound edges expressed by one of the member's tweets. Pure; never raises."""
    member_id = str(member_id)
    tid = str(t.get("id") or "")
    if not tid:
        return []
    # Defensive: only attribute content actually authored by the polled member.
    author_id = str((t.get("author") or {}).get("id") or "")
    if author_id and author_id != member_id:
        return []

    ts = _parse_dt(t.get("createdAt"))
    out: list[dict] = []
    seen: set[str] = set()  # target_id — mirrors unique(tweet_id, target_id)

    def add(kind: str, target_id, target_username) -> None:
        target = str(target_id or "")
        if not target or target == member_id or target in seen:
            return
        seen.add(target)
        uname = str(target_username or "").lstrip("@").lower()
        out.append({
            "tweet_id": tid, "engager_id": member_id, "target_id": target,
            "target_username": uname or None, "kind": kind, "ts": ts, "day": ts.date(),
        })

    # retweet first (most specific wrapper), then reply, then quote — a tweet can be
    # reply+quote at once (two different targets -> two edges).
    rt = t.get("retweeted_tweet") or t.get("retweetedTweet")
    if isinstance(rt, dict):
        a = rt.get("author") or {}
        add("retweet", a.get("id"), a.get("userName") or a.get("screen_name"))
    if t.get("isReply") or t.get("inReplyToUserId"):
        add("reply", t.get("inReplyToUserId"), t.get("inReplyToUsername"))
    qt = t.get("quoted_tweet") or t.get("quotedTweet")
    if isinstance(qt, dict):
        a = qt.get("author") or {}
        add("quote", a.get("id"), a.get("userName") or a.get("screen_name"))
    return out


async def extract_edges(batch_size: int = 500) -> dict:
    """Derive edges from all unparsed bronze rows. Idempotent; resumes after any crash."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    tweets_parsed = edges_inserted = 0
    while True:
        async with SessionLocal() as s:
            raws = (await s.execute(
                select(EngTweetRaw).where(EngTweetRaw.parsed.is_(False)).limit(batch_size)
            )).scalars().all()
            if not raws:
                break

            candidates: dict[tuple[str, str], dict] = {}
            for r in raws:
                for e in edges_from_tweet(r.raw or {}, r.member_id):
                    candidates.setdefault((e["tweet_id"], e["target_id"]), e)
                r.parsed = True

            # pre-filter already-persisted (tweet_id, target_id) pairs -> idempotent replay
            existing: set[tuple[str, str]] = set()
            tids = list({k[0] for k in candidates})
            for i in range(0, len(tids), 400):
                rows = (await s.execute(
                    select(EngEdge.tweet_id, EngEdge.target_id)
                    .where(EngEdge.tweet_id.in_(tids[i:i + 400])))).all()
                existing |= {(a, b) for a, b in rows}

            fresh = [EngEdge(**e) for k, e in candidates.items() if k not in existing]
            s.add_all(fresh)
            await s.commit()  # parsed flags + edges land atomically per batch

            tweets_parsed += len(raws)
            edges_inserted += len(fresh)

    return {"tweets_parsed": tweets_parsed, "edges_inserted": edges_inserted}
