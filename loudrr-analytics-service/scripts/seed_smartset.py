"""Load the clean master_seed (3,894 corroborated anchors) into smart_set as the crawl
seed, replacing the old pre-cleanup bootstrap. Preserves last_crawled_at for any member
already crawled (so we don't re-spend on them). The rich multi-category label lives in
master_seed; smart_set.category (String16) just holds a placeholder — the crawl/PageRank
don't use it. python -m scripts.seed_smartset
"""
import asyncio

from sqlalchemy import select, delete

from app.db.models import SmartSetMember
from app.db.session import SessionLocal, engine, Base


async def main():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    import sqlite3
    c = sqlite3.connect("data/harvest.db").cursor()
    seed = c.execute("SELECT user_id, username, name FROM master_seed").fetchall()
    c.connection.close()

    async with SessionLocal() as s:
        # preserve crawl progress for members that survive into the new seed
        done = {u for (u,) in (await s.execute(
            select(SmartSetMember.user_id).where(SmartSetMember.last_crawled_at.isnot(None)))).all()}
        await s.execute(delete(SmartSetMember))
        await s.flush()
        kept = 0
        for uid, un, name in seed:
            m = SmartSetMember(user_id=uid, username=un, display_name=name,
                               category="unknown", is_seed=True, seed_source="master_v2")
            if uid in done:
                from sqlalchemy import func
                m.last_crawled_at = func.now()  # already crawled -> keep done
                kept += 1
            s.add(m)
        await s.commit()
    print(f"smart_set rebuilt: {len(seed)} seed members (is_seed=True); "
          f"{kept} preserved as already-crawled")


if __name__ == "__main__":
    asyncio.run(main())
