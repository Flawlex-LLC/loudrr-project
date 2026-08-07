"""Author reputation weights for mindshare — our OWN PageRank, written to ms_roster (NOT smart_set).

Kaito weights a voice by smart-follower reach; our equivalent is PageRank over the follow graph.
This REPLICATES `app/services/score.py`'s personalized PageRank but is fully read-only on your data
(`smart_set`, `edges`) and writes ONLY `ms_roster.weight` — it never touches `smart_set.score`,
`score_snapshots`, or the scoring service. Run after a roster rebuild (the graph is static between
crawls, so this is setup, not part of the hourly tick).

Weights are NORMALIZED to mean = 1.0, so a roster KOL we haven't crawled (no PageRank) gets the
neutral floor of 1.0 in the scorer, while a high-reputation account scales above 1.0.
"""
from __future__ import annotations

import argparse
import asyncio
import logging

import networkx as nx
from sqlalchemy import select

from app.core.config import settings
from app.db.models import Edge, SmartSetMember
from app.db.session import Base, SessionLocal, engine
from app.mindshare.models import MsRoster

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("ms.weights")

WEIGHT_CAP = 25.0   # clamp extreme outliers so one mega-account can't dominate a niche


async def compute_weights(vertical: str = "crypto") -> dict:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # --- READ-ONLY: smart_set members, seeds, M-internal edges ---
    async with SessionLocal() as s:
        member_ids = set((await s.execute(select(SmartSetMember.user_id))).scalars())
        seed_ids = set((await s.execute(
            select(SmartSetMember.user_id).where(SmartSetMember.is_seed.is_(True)))).scalars())
        edge_rows = (await s.execute(
            select(Edge.follower_id, Edge.followee_id).where(
                Edge.followee_id.in_(member_ids)))).all()

    if not member_ids:
        return {"note": "smart_set empty — nothing to weight", "members": 0}

    g = nx.DiGraph()
    g.add_nodes_from(member_ids)
    g.add_edges_from(edge_rows)
    personalization = {n: (1.0 if n in seed_ids else 0.0) for n in g.nodes} if seed_ids else None
    if personalization and sum(personalization.values()) == 0:
        personalization = None

    pr = nx.pagerank(g, alpha=settings.pagerank_alpha, max_iter=settings.pagerank_max_iter,
                     personalization=personalization, dangling=None)

    # normalize to mean = 1.0, clamp outliers
    mean = (sum(pr.values()) / len(pr)) if pr else 0.0
    norm = {uid: min(WEIGHT_CAP, sc / mean) for uid, sc in pr.items()} if mean > 0 else {}

    # --- WRITE: only ms_roster.weight, for this vertical's KOL authors ---
    async with SessionLocal() as s:
        kols = (await s.execute(select(MsRoster).where(
            MsRoster.vertical == vertical, MsRoster.role == "kol"))).scalars().all()
        scored = 0
        for r in kols:
            w = norm.get(r.entity_id)
            r.weight = w
            r.in_smart_set = r.entity_id in member_ids
            if w is not None:
                scored += 1
        await s.commit()

    return {"vertical": vertical, "members": len(member_ids), "edges": len(edge_rows),
            "roster_kols": len(kols), "kols_weighted": scored,
            "max_weight": round(max(norm.values()), 2) if norm else 0,
            "seeded": bool(personalization)}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vertical", default="crypto")
    args = ap.parse_args()
    stats = await compute_weights(args.vertical)
    logger.info("weights computed: %s", stats)
    print(stats)


if __name__ == "__main__":
    asyncio.run(main())
