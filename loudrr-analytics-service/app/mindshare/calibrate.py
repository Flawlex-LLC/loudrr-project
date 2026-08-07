"""Calibrate the mindshare scoring model against Kaito ground truth (read-only, in-memory).

Loads the ingested tweets + roster ONCE, then recomputes crypto token mindshare in-memory for many
scoring configs (engagement weights, log-dampening, recency half-life) and scores each by parity vs
the scraped Kaito leaderboard (top-10/20 overlap + Spearman over Kaito's top-20). Prints the configs
ranked best-first so we can lock the winner into score.py. Mutates nothing.

    python -m app.mindshare.calibrate --vertical crypto --sector ALL --window 7d
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text

from app.db.session import SessionLocal
from app.mindshare.attribute import attribute_tweet, build_token_index
from app.mindshare.models import MsRoster, MsTweetRaw

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("ms.calibrate")

WINDOW_H = {"24h": 24, "7d": 168, "30d": 720}

# configs to sweep: (label, engagement-fn, recency half-life hours or None)
def _eng_linear(t, w):
    return (w["like"] * t.like_count + w["rt"] * t.retweet_count + w["quote"] * t.quote_count
            + w["reply"] * t.reply_count + w["view"] * t.view_count)

def _eng_log(t, w):
    # log-dampened: a viral tweet counts a lot but not unboundedly (closer to Kaito's spread)
    return (w["like"] * math.log1p(t.like_count) + w["rt"] * math.log1p(t.retweet_count)
            + w["quote"] * math.log1p(t.quote_count) + w["reply"] * math.log1p(t.reply_count)
            + w["view"] * math.log1p(t.view_count))

W_BASE = {"like": 1.0, "rt": 3.0, "quote": 2.0, "reply": 1.0, "view": 0.0005}
W_REACH = {"like": 1.0, "rt": 3.0, "quote": 2.0, "reply": 0.5, "view": 0.002}

CONFIGS = [
    ("linear/base/no-recency", _eng_linear, W_BASE, None),
    ("linear/base/halflife-48h", _eng_linear, W_BASE, 48),
    ("linear/base/halflife-24h", _eng_linear, W_BASE, 24),
    ("log/base/no-recency", _eng_log, W_BASE, None),
    ("log/base/halflife-48h", _eng_log, W_BASE, 48),
    ("log/reach/halflife-48h", _eng_log, W_REACH, 48),
    ("log/reach/halflife-24h", _eng_log, W_REACH, 24),
    ("linear/reach/halflife-48h", _eng_linear, W_REACH, 48),
]


def _spearman(rank_a: dict, rank_b: dict, keys: list[str]) -> float:
    """Spearman ρ over the given keys (lower rank = better). Missing -> worst rank."""
    n = len(keys)
    if n < 3:
        return 0.0
    worst = n + 1
    a = [rank_a.get(k, worst) for k in keys]
    b = [rank_b.get(k, worst) for k in keys]
    ma, mb = sum(a) / n, sum(b) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    va = math.sqrt(sum((x - ma) ** 2 for x in a))
    vb = math.sqrt(sum((y - mb) ** 2 for y in b))
    return cov / (va * vb) if va and vb else 0.0


async def calibrate(vertical: str, sector: str, window: str) -> None:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    cutoff = now - timedelta(hours=WINDOW_H[window])

    async with SessionLocal() as s:
        tokens = (await s.execute(select(MsRoster).where(
            MsRoster.vertical == vertical, MsRoster.role == "token"))).scalars().all()
        idx = build_token_index(tokens)
        weights = {r.entity_id: (r.weight if (r.weight and r.weight > 0) else 1.0)
                   for r in (await s.execute(select(MsRoster).where(
                       MsRoster.vertical == vertical, MsRoster.role == "kol"))).scalars().all()}
        tweets = [t for t in (await s.execute(select(MsTweetRaw))).scalars().all()
                  if t.raw and t.created_at and _naive(t.created_at) >= cutoff]
        rid = (await s.execute(text("SELECT MAX(run_id) FROM kaito_mindshare"))).scalar()
        krows = (await s.execute(text(
            "SELECT entity_id, rank FROM kaito_mindshare WHERE run_id=:r AND kind='companies' "
            "AND vertical=:v AND sector=:s AND duration='7d' ORDER BY rank"),
            {"r": rid, "v": vertical, "s": sector})).all()
    kaito_rank = {e: r for e, r in krows}
    kaito_top = [e for e, _ in krows]
    logger.info("tweets in window=%d  tokens=%d  kaito_entities=%d", len(tweets), len(tokens), len(kaito_top))

    results = []
    for label, eng_fn, w, halflife in CONFIGS:
        per_sector = defaultdict(float)
        # attribute each tweet, accumulate weighted value for the target sector
        for t in tweets:
            hits = attribute_tweet(t.raw, idx)
            if not hits:
                continue
            base = eng_fn(t, w) * weights.get(t.author_id, 1.0)
            if t.is_retweet:
                base *= 0.3
            if halflife:
                age_h = (now - _naive(t.created_at)).total_seconds() / 3600.0
                base *= 0.5 ** (age_h / halflife)
            for eid, sectors in hits.items():
                if sector in sectors:
                    per_sector[eid] += base
        ranked = sorted(per_sector, key=lambda e: per_sector[e], reverse=True)
        our_rank = {e: i + 1 for i, e in enumerate(ranked)}
        ov10 = len(set(ranked[:10]) & set(kaito_top[:10]))
        ov20 = len(set(ranked[:20]) & set(kaito_top[:20]))
        rho = _spearman(our_rank, kaito_rank, kaito_top[:20])
        results.append((ov10, ov20, round(rho, 3), label, ranked[:10]))

    results.sort(reverse=True)
    print(f"\n=== calibration {vertical}/{sector}/{window}  (kaito top10: {kaito_top[:10]}) ===")
    print(f"{'ov@10':>5} {'ov@20':>5} {'rho':>6}  config")
    for ov10, ov20, rho, label, top in results:
        print(f"{ov10:>5} {ov20:>5} {rho:>6}  {label:28} {top[:8]}")


def _naive(dt):
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vertical", default="crypto")
    ap.add_argument("--sector", default="ALL")
    ap.add_argument("--window", default="7d", choices=list(WINDOW_H))
    args = ap.parse_args()
    await calibrate(args.vertical, args.sector, args.window)


if __name__ == "__main__":
    asyncio.run(main())
