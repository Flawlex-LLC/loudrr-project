"""Bronze ingest: pull roster KOLs' recent tweets + engagement via the gateway, incrementally.

    python -m app.mindshare.ingest --vertical crypto --max-accounts 10 --max-pages 1   # test
    python -m app.mindshare.ingest --vertical crypto                                    # full

For each author in ``ms_roster`` (role='kol') we fetch tweets newer than its ``ms_ingest_cursor``
(`/user/last_tweets`, newest-first, stop at since_id), upsert into ``ms_tweet_raw`` (idempotent on
tweet_id), and advance the cursor. Append-only + incremental so re-runs are cheap and replayable.
Routed entirely through the loudrr gateway (verified to proxy the tweet endpoints).
"""
from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select, text

from app.clients.twitterapi import TwitterAPIClient
from app.core.util import parse_twitter_dt as _parse_dt
from app.db.session import Base, SessionLocal, engine
from app.mindshare.models import MsIngestCursor, MsRoster, MsTweetRaw

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("ms.ingest")


def _to_raw(t: dict, account_id: str) -> MsTweetRaw | None:
    """``account_id`` is the roster KOL whose timeline surfaced this tweet — we store THAT as the
    author (the voice we attribute mindshare to), even for retweets (original stays in ``raw``)."""
    tid = str(t.get("id") or "")
    if not tid:
        return None
    return MsTweetRaw(
        tweet_id=tid,
        author_id=str(account_id),
        created_at=_parse_dt(t.get("createdAt")),
        text=(t.get("text") or "")[:4096],
        lang=t.get("lang"),
        like_count=int(t.get("likeCount") or 0),
        retweet_count=int(t.get("retweetCount") or 0),
        reply_count=int(t.get("replyCount") or 0),
        quote_count=int(t.get("quoteCount") or 0),
        view_count=int(t.get("viewCount") or 0),
        is_retweet=bool(t.get("isRetweet")),
        raw=t,
    )


async def ingest_vertical(vertical: str = "crypto", *, max_pages: int = 3,
                          max_accounts: int | None = None, concurrency: int = 6,
                          tweets_qps: int = 8) -> dict:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with SessionLocal() as s:
        authors = (await s.execute(
            select(MsRoster.entity_id).where(MsRoster.vertical == vertical, MsRoster.role == "kol",
                                             MsRoster.active.is_(True)).distinct()
        )).scalars().all()
        if max_accounts:
            authors = authors[:max_accounts]
        cursors = {c.author_id: c for c in (await s.execute(select(MsIngestCursor))).scalars().all()}

    # Tweet pulls are light — use a dedicated QPS, NOT the crawl's 8/min follow-graph pacing.
    client = TwitterAPIClient(qps=tweets_qps)
    sem = asyncio.Semaphore(concurrency)
    results: dict[str, tuple[list[MsTweetRaw], str | None]] = {}

    async def pull(author_id: str):
        since = cursors.get(author_id).since_id if author_id in cursors else None
        rows, newest = [], since
        async with sem:
            try:
                async for t in client.iter_user_tweets(user_id=author_id, since_id=since,
                                                        max_pages=max_pages):
                    r = _to_raw(t, author_id)
                    if r is None:
                        continue
                    rows.append(r)
                    if newest is None or (r.tweet_id.isdigit() and int(r.tweet_id) > int(newest or 0)):
                        newest = r.tweet_id
            except Exception as e:  # noqa: BLE001 - one bad account shouldn't kill the run
                logger.warning("ingest %s: %s", author_id, e)
        results[author_id] = (rows, newest)

    await asyncio.gather(*(pull(a) for a in authors))

    # dedup within batch + against existing, then insert; advance cursors
    by_id: dict[str, MsTweetRaw] = {}
    for rows, _ in results.values():
        for r in rows:
            by_id.setdefault(r.tweet_id, r)
    candidate_ids = list(by_id)
    inserted = 0
    async with SessionLocal() as s:
        existing: set[str] = set()
        for i in range(0, len(candidate_ids), 400):
            chunk = candidate_ids[i:i + 400]
            existing |= set((await s.execute(
                select(MsTweetRaw.tweet_id).where(MsTweetRaw.tweet_id.in_(chunk)))).scalars().all())
        fresh = [r for tid, r in by_id.items() if tid not in existing]
        s.add_all(fresh)
        inserted = len(fresh)

        now = datetime.now(timezone.utc)
        for author_id, (rows, newest) in results.items():
            cur = cursors.get(author_id)
            if cur is None:
                s.add(MsIngestCursor(author_id=author_id, since_id=newest, last_run_at=now,
                                     tweets_seen=len(rows)))
            else:
                cur.since_id = newest or cur.since_id
                cur.last_run_at = now
                cur.tweets_seen = (cur.tweets_seen or 0) + len(rows)
                await s.merge(cur)
        await s.commit()

    return {"vertical": vertical, "accounts": len(authors),
            "tweets_fetched": sum(len(r) for r, _ in results.values()),
            "tweets_inserted": inserted, "credits_spent": round(client.credits_spent, 1),
            "usd_spent": round(client.usd_spent, 4)}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vertical", default="crypto")
    ap.add_argument("--max-pages", type=int, default=3)
    ap.add_argument("--max-accounts", type=int, default=None)
    ap.add_argument("--concurrency", type=int, default=6)
    args = ap.parse_args()
    stats = await ingest_vertical(args.vertical, max_pages=args.max_pages,
                                  max_accounts=args.max_accounts, concurrency=args.concurrency)
    logger.info("ingest done: %s", stats)
    print(stats)


if __name__ == "__main__":
    asyncio.run(main())
