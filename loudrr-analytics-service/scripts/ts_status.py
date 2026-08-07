"""TwitterScore harvest status — read-only counts. python -m scripts.ts_status"""
import asyncio
from sqlalchemy import select, func
from app.db.session import SessionLocal
from app.db.models import TwitterScoreAccount as T, SmartSetMember as M


async def main():
    async with SessionLocal() as s:
        total = (await s.execute(select(func.count()).select_from(T))).scalar()
        tagged = (await s.execute(select(func.count()).select_from(T)
                  .where(func.coalesce(T.tags, "") != ""))).scalar()
        cat = (await s.execute(select(func.count()).select_from(T)
               .where(func.coalesce(T.categories, "") != ""))).scalar()
        scored = (await s.execute(select(func.count()).select_from(T)
                  .where(T.twitterscore.isnot(None)))).scalar()
        # how many TS accounts are NOT already in smart_set (= newly discovered)
        member_ids = set((await s.execute(select(M.user_id))).scalars())
        ts_ids = set((await s.execute(select(T.user_id))).scalars())
        new = len(ts_ids - member_ids)
    print(f"twitterscore_accounts: {total:,}")
    print(f"  with TwitterScore:   {scored:,}")
    print(f"  with TAGS:           {tagged:,}")
    print(f"  with CATEGORIES:     {cat:,}")
    print(f"  NEW (not in smart_set / would expand M): {new:,}")
    print(f"smart_set members: {len(member_ids):,}")


if __name__ == "__main__":
    asyncio.run(main())
