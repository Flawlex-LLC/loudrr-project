"""Build set M per niche from the scraped Kaito rosters, reconciled to our PageRank weights.

    python -m app.mindshare.roster --vertical crypto

For a vertical we take, from the latest ``kaito_mindshare`` run:
  * role='kol'   — every tracked voice per sector (the *authors* we'll ingest tweets from),
                   LEFT-JOINed to ``smart_set`` so each carries its PageRank ``weight`` (NULL = we
                   haven't crawled/scored it yet → a backfill target).
  * role='token' — every tracked company/ticker per sector (what mentions get *attributed* to).

Rosters are slow-changing, so a rebuild is a clean delete-then-insert for that (vertical, source).
This is the prerequisite for ingest: it defines exactly whose tweets we pull and what we score.
"""
from __future__ import annotations

import argparse
import asyncio
import logging

from sqlalchemy import delete, text

from app.db.session import Base, SessionLocal, engine
from app.mindshare.models import MsRoster

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("ms.roster")

_KOL_SQL = text("""
    SELECT km.sector AS sector, km.entity_id AS entity_id,
           MAX(km.handle) AS handle, MAX(km.name) AS name,
           ss.score AS weight,
           CASE WHEN ss.user_id IS NOT NULL THEN 1 ELSE 0 END AS in_ss
    FROM kaito_mindshare km
    LEFT JOIN smart_set ss ON ss.user_id = km.entity_id
    WHERE km.run_id = :rid AND km.kind = 'voices' AND km.vertical = :v
          AND km.entity_id IS NOT NULL
    GROUP BY km.sector, km.entity_id, ss.score, ss.user_id
""")

_TOKEN_SQL = text("""
    SELECT km.sector AS sector, km.entity_id AS entity_id, MAX(km.name) AS name
    FROM kaito_mindshare km
    WHERE km.run_id = :rid AND km.kind = 'companies' AND km.vertical = :v
          AND km.entity_id IS NOT NULL
    GROUP BY km.sector, km.entity_id
""")


async def rebuild_roster(vertical: str = "crypto") -> dict:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with SessionLocal() as s:
        rid = (await s.execute(text("SELECT MAX(run_id) FROM kaito_mindshare"))).scalar()
        if not rid:
            raise RuntimeError("no kaito_mindshare data — run scripts.scrape_kaito first")

        kols = (await s.execute(_KOL_SQL, {"rid": rid, "v": vertical})).mappings().all()
        tokens = (await s.execute(_TOKEN_SQL, {"rid": rid, "v": vertical})).mappings().all()

        # clean rebuild for this (vertical, source)
        await s.execute(delete(MsRoster).where(MsRoster.vertical == vertical,
                                               MsRoster.source == "kaito"))
        rows = [
            MsRoster(vertical=vertical, sector=r["sector"], role="kol", entity_id=str(r["entity_id"]),
                     handle=r["handle"], name=r["name"], weight=r["weight"],
                     in_smart_set=bool(r["in_ss"]), source="kaito")
            for r in kols
        ] + [
            MsRoster(vertical=vertical, sector=r["sector"], role="token", entity_id=str(r["entity_id"]),
                     symbol=str(r["entity_id"]), name=r["name"], source="kaito")
            for r in tokens
        ]
        s.add_all(rows)
        await s.commit()

    n_kol = sum(1 for r in rows if r.role == "kol")
    n_tok = sum(1 for r in rows if r.role == "token")
    dist_kol = len({r.entity_id for r in rows if r.role == "kol"})
    scored = sum(1 for r in rows if r.role == "kol" and r.weight is not None)
    return {"run_id": rid, "vertical": vertical, "kol_rows": n_kol, "distinct_kols": dist_kol,
            "token_rows": n_tok, "kols_with_pagerank": scored}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vertical", default="crypto")
    args = ap.parse_args()
    stats = await rebuild_roster(args.vertical)
    logger.info("roster rebuilt: %s", stats)
    print(stats)


if __name__ == "__main__":
    asyncio.run(main())
