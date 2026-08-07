"""Silver: turn raw tweets into scored attention contributions per (tweet, entity).

    value = engagement(tweet) × author_weight × retweet_factor

  * engagement   = likes + 3·retweets + 2·quotes + replies + 0.0005·views  (tunable below)
  * author_weight = the author's PageRank weight (ms_roster.weight); a floor of 1.0 when we haven't
                    scored them yet, so the niche still gets signal until PageRank is recomputed.
  * retweet_factor = 0.3 (amplification counts less than origination).

Processes only tweets not yet scored (idempotent), so it's incremental like ingest. Recency is
handled downstream by the hourly buckets/windows, not here.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import math

from sqlalchemy import select

from app.db.session import Base, SessionLocal, engine
from app.mindshare.attribute import attribute_tweet, build_token_index
from app.mindshare.models import MsContribution, MsRoster, MsTweetRaw

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("ms.score")

# engagement weights (tunable; relative scale only — mindshare normalizes per niche).
# LOG-DAMPENED (calibrated vs Kaito 2026-06-30): share-of-voice is about breadth of discussion,
# not a single viral tweet — log1p stops one mega-tweet from dominating a niche (lifted Kaito
# top-10 overlap 6 -> 9). Recency decay is applied at aggregate time (see aggregate.RECENCY_HALFLIFE_H).
W_LIKE, W_RT, W_QUOTE, W_REPLY, W_VIEW = 1.0, 3.0, 2.0, 1.0, 0.0005
RETWEET_FACTOR = 0.3
WEIGHT_FLOOR = 1.0   # author_weight when PageRank weight is missing/0 (normalized to mean=1)


def engagement(t: MsTweetRaw) -> float:
    return (W_LIKE * math.log1p(t.like_count) + W_RT * math.log1p(t.retweet_count)
            + W_QUOTE * math.log1p(t.quote_count) + W_REPLY * math.log1p(t.reply_count)
            + W_VIEW * math.log1p(t.view_count))


async def score_vertical(vertical: str = "crypto") -> dict:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with SessionLocal() as s:
        tokens = (await s.execute(select(MsRoster).where(
            MsRoster.vertical == vertical, MsRoster.role == "token"))).scalars().all()
        idx = build_token_index(tokens)
        weights = {r.entity_id: (r.weight if (r.weight and r.weight > 0) else WEIGHT_FLOOR)
                   for r in (await s.execute(select(MsRoster).where(
                       MsRoster.vertical == vertical, MsRoster.role == "kol"))).scalars().all()}
        scored = set((await s.execute(
            select(MsContribution.tweet_id).distinct())).scalars().all())
        tweets = (await s.execute(select(MsTweetRaw))).scalars().all()

        contribs, n_matched = [], 0
        for t in tweets:
            if t.tweet_id in scored or not t.raw:
                continue
            hits = attribute_tweet(t.raw, idx)
            if not hits:
                continue
            n_matched += 1
            eng = engagement(t)
            aw = weights.get(t.author_id, WEIGHT_FLOOR)
            rf = RETWEET_FACTOR if t.is_retweet else 1.0
            value = eng * aw * rf
            for eid, sectors in hits.items():
                for sector in sectors:
                    contribs.append(MsContribution(
                        tweet_id=t.tweet_id, author_id=t.author_id, vertical=vertical,
                        sector=sector, entity_id=eid, value=value, ts=t.created_at))
        s.add_all(contribs)
        await s.commit()

    return {"vertical": vertical, "tweets_considered": len(tweets),
            "tweets_matched": n_matched, "contributions": len(contribs)}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vertical", default="crypto")
    args = ap.parse_args()
    stats = await score_vertical(args.vertical)
    logger.info("score done: %s", stats)
    print(stats)


if __name__ == "__main__":
    asyncio.run(main())
