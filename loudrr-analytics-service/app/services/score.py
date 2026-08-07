"""Score job (research §3, §11 step 4) — the compute layer, zero scraping.

Two pieces:
  1. ``recompute_member_scores`` (batch): personalized PageRank over the M-internal
     follow graph -> each smart-set member gets a weight. This is the engine Sorsa
     never names but describes exactly ("influential to the degree influential
     accounts follow it, iterated"). Personalization is biased toward the seed so
     the moat (curated anchors) anchors the ranking.
  2. ``score_for`` / ``followers_stats`` / ``top_followers`` (query-time): for ANY
     account X, score(X) = weighted sum of the scores of the M-members who follow X,
     read straight off the reverse index. No scraping, no scan of X's own followers.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import networkx as nx
from sqlalchemy import func, select, update

from app.core.config import settings
from app.db.models import Category, Edge, ScoreSnapshot, SmartSetMember
from app.db.session import SessionLocal

logger = logging.getLogger(__name__)


async def recompute_member_scores() -> dict:
    """Run personalized PageRank over the M-internal graph; persist member scores."""
    async with SessionLocal() as session:
        member_ids = set(
            (await session.execute(select(SmartSetMember.user_id))).scalars()
        )
        seed_ids = set(
            (
                await session.execute(
                    select(SmartSetMember.user_id).where(SmartSetMember.is_seed.is_(True))
                )
            ).scalars()
        )
        # M-internal edges only: both endpoints are smart-set members.
        edge_rows = (
            await session.execute(
                select(Edge.follower_id, Edge.followee_id).where(
                    Edge.followee_id.in_(member_ids)
                )
            )
        ).all()

    if not member_ids:
        return {"members": 0, "edges": 0, "note": "smart_set empty — seed first"}

    g = nx.DiGraph()
    g.add_nodes_from(member_ids)
    # edge m -> X means m follows X; influence flows from follower to followee, so
    # PageRank on this graph ranks accounts by who-follows-them (what we want).
    g.add_edges_from(edge_rows)

    personalization = {n: (1.0 if n in seed_ids else 0.0) for n in g.nodes} if seed_ids else None
    if personalization and sum(personalization.values()) == 0:
        personalization = None

    scores = nx.pagerank(
        g,
        alpha=settings.pagerank_alpha,
        max_iter=settings.pagerank_max_iter,
        personalization=personalization,
        dangling=None,
    )

    # persist (batched updates) + snapshot for /score-changes deltas
    async with SessionLocal() as session:
        for uid, sc in scores.items():
            await session.execute(
                update(SmartSetMember).where(SmartSetMember.user_id == uid).values(score=sc)
            )
        session.add_all(
            [ScoreSnapshot(user_id=uid, score=sc) for uid, sc in scores.items()]
        )
        await session.commit()

    logger.info("scored %d members over %d internal edges", len(scores), len(edge_rows))
    return {"members": len(scores), "edges": len(edge_rows), "seeded": bool(personalization)}


# ---- query-time reads (Sorsa-compatible) -------------------------------

def _apply_cutoff(stmt):
    """Restrict to the top-N smart-set tier by pr_rank when settings.smart_set_cutoff is set
    (None = all ~98k, the historical behavior). NULL pr_rank (e.g. unranked test fixtures) is
    kept so the cut never silently drops rows that predate ranking."""
    cutoff = settings.smart_set_cutoff
    if cutoff:
        stmt = stmt.where(
            (SmartSetMember.pr_rank.is_(None)) | (SmartSetMember.pr_rank <= cutoff)
        )
    return stmt


async def _followers_in_M(user_id: str):
    """The smart-set members who follow ``user_id`` (joined to their score/category)."""
    async with SessionLocal() as session:
        rows = (
            await session.execute(
                _apply_cutoff(
                    select(SmartSetMember)
                    .join(Edge, Edge.follower_id == SmartSetMember.user_id)
                    .where(Edge.followee_id == user_id)
                )
            )
        ).scalars()
        return list(rows)


def _display(raw: float) -> float:
    """Map normalized weight onto Sorsa's thousands-scale. Calibrated against
    ground-truth once a working Sorsa key is available (see clients/sorsa.py)."""
    return round(raw * settings.score_display_scale, 2)


async def score_for(user_id: str) -> dict:
    """score(X) = Σ scores of M-members following X (research §6), as ONE indexed aggregate.
    A mega-account has tens of thousands of smart followers, so we sum/count in SQL via the
    edges(followee_id) index instead of loading every follower row into Python (the old path
    took ~4s for Elon's 55k followers)."""
    async with SessionLocal() as session:
        raw, n = (
            await session.execute(
                _apply_cutoff(
                    select(func.coalesce(func.sum(SmartSetMember.score), 0.0), func.count())
                    .select_from(Edge)
                    .join(SmartSetMember, SmartSetMember.user_id == Edge.follower_id)
                    .where(Edge.followee_id == user_id)
                )
            )
        ).one()
    raw = float(raw)
    return {"score": _display(raw), "raw": raw, "smart_followers": int(n)}


async def followers_stats(user_id: str) -> dict:
    """Sorsa /followers-stats shape: counts of categorized followers (NOT the real
    follower count). Collapses our 8 categories to Sorsa's 3 + a richer breakdown."""
    followers = await _followers_in_M(user_id)
    by_cat: dict[str, int] = {}
    for m in followers:
        by_cat[m.category] = by_cat.get(m.category, 0) + 1
    influencers = by_cat.get(Category.INFLUENCER.value, 0) + by_cat.get(Category.FOUNDER.value, 0) \
        + by_cat.get(Category.ANGEL.value, 0)
    return {
        "followers_count": len(followers),          # categorized count, per Sorsa semantics
        "influencers_count": influencers,
        "projects_count": by_cat.get(Category.PROJECT.value, 0),
        "venture_capitals_count": by_cat.get(Category.VC.value, 0),
        "user_protected": False,
        "breakdown": by_cat,                          # our richer extra
    }


async def top_followers(user_id: str, k: int = 20) -> list[dict]:
    """Top-k smart followers of X by score (Sorsa /top-followers)."""
    followers = sorted(await _followers_in_M(user_id), key=lambda m: m.score, reverse=True)[:k]
    return [
        {
            "id": m.user_id,
            "username": m.username,
            "display_name": m.display_name,
            "category": m.category,
            "followers_count": m.followers_count,
            "score": _display(m.score),
        }
        for m in followers
    ]


async def score_changes(user_id: str) -> dict:
    """week/month deltas from score snapshots (research §2)."""
    async with SessionLocal() as session:
        latest = (
            await session.execute(
                select(ScoreSnapshot.score)
                .where(ScoreSnapshot.user_id == user_id)
                .order_by(ScoreSnapshot.captured_at.desc())
                .limit(1)
            )
        ).scalar()
        if latest is None:
            return {"week_delta": 0.0, "month_delta": 0.0}

        now = datetime.now(timezone.utc)

        def _score_at(days: int):
            cutoff = now - timedelta(days=days)
            return (
                select(ScoreSnapshot.score)
                .where(ScoreSnapshot.user_id == user_id, ScoreSnapshot.captured_at <= cutoff)
                .order_by(ScoreSnapshot.captured_at.desc())
                .limit(1)
            )

        wk = (await session.execute(_score_at(7))).scalar()
        mo = (await session.execute(_score_at(30))).scalar()
    return {
        "week_delta": _display(latest - wk) if wk is not None else 0.0,
        "month_delta": _display(latest - mo) if mo is not None else 0.0,
    }
