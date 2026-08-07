"""Review EVERYTHING we capture from TwitterScore (DB-direct, read-only).
    python -m scripts.ts_show
"""
import asyncio
import sys
from sqlalchemy import select, func, or_
from app.db.session import SessionLocal
from app.db.models import TwitterScoreAccount as T, TwitterScoreFollow as F, SmartSetMember as M

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FIELDS = [
    ("user_id", "X/Twitter numeric id"),
    ("username", "@handle"),
    ("name", "display name"),
    ("twitterscore", "TwitterScore 0-1000"),
    ("band", "Excellent/Good/Normal/Bad"),
    ("categories", "account type (Founders, Influencers, Venture Capitals...)"),
    ("tags", "VC/ecosystem affiliations (a16z (Tier 1 VC); Ethereum (Ecosystems))"),
    ("description", "bio"),
    ("based_in", "country/region"),
    ("joined_date", "X join date"),
    ("followers", "real follower count"),
    ("smart_followers", "# of smart/notable followers"),
    ("seen_count", "# accounts that list it as a significant follower (in-degree, like Sorsa)"),
    ("source", "profile | followedby"),
]


async def main():
    async with SessionLocal() as s:
        total = (await s.execute(select(func.count()).select_from(T))).scalar()
        async def c(cond):
            return (await s.execute(select(func.count()).select_from(T).where(cond))).scalar()
        scored = await c(T.twitterscore.isnot(None))
        tagged = await c(func.coalesce(T.tags, "") != "")
        catd = await c(func.coalesce(T.categories, "") != "")
        descd = await c(func.coalesce(T.description, "") != "")
        basedd = await c(func.coalesce(T.based_in, "") != "")
        edges = (await s.execute(select(func.count()).select_from(F))).scalar()
        member_ids = set((await s.execute(select(M.user_id))).scalars())
        ts_ids = set((await s.execute(select(T.user_id))).scalars())
        new = len(ts_ids - member_ids)
        # QUALITY: score distribution (overall + NEW-only) to expose any garbage
        async def bucket(lo, hi, new_only=False):
            q = select(func.count()).select_from(T)
            if lo is not None:
                q = q.where(T.twitterscore >= lo)
            if hi is not None:
                q = q.where(T.twitterscore < hi)
            if new_only:
                q = q.where(T.user_id.notin_(member_ids))
            return (await s.execute(q)).scalar()
        no_score = await c(T.twitterscore.is_(None))
        buckets = {}
        for lo, hi, lbl in [(None, 50, "<50 (low)"), (50, 200, "50-200"),
                            (200, 500, "200-500"), (500, 1001, "500-1000 (top)")]:
            buckets[lbl] = (await bucket(lo, hi), await bucket(lo, hi, new_only=True))
        # richest rows: have BOTH category and tags
        rich = (await s.execute(select(T).where(func.coalesce(T.categories, "") != "",
                func.coalesce(T.tags, "") != "").order_by(T.twitterscore.desc()).limit(6))).scalars().all()
        # a few edges (followee <- follower)
        edge_rows = (await s.execute(select(F.followee_id, F.follower_id, F.category).limit(6))).all()

    print("=" * 78)
    print("FIELDS WE CAPTURE PER ACCOUNT (table: twitterscore_accounts)")
    print("=" * 78)
    for name, desc in FIELDS:
        print(f"  {name:16} - {desc}")
    print("\n" + "=" * 78)
    print("COVERAGE (live; snowball still running)")
    print("=" * 78)
    print(f"  accounts total        {total:,}")
    print(f"  with TwitterScore     {scored:,}")
    print(f"  with tags             {tagged:,}")
    print(f"  with categories       {catd:,}")
    print(f"  with description      {descd:,}")
    print(f"  with based_in         {basedd:,}")
    print(f"  NEW (not in smart_set){new:,}")
    print(f"  follow-edges          {edges:,}")
    print(f"  no score yet          {no_score:,}")
    print("\n" + "=" * 78)
    print("QUALITY: TwitterScore distribution  (all accounts | NEW-only)")
    print("=" * 78)
    print("  (garbage check: are we pulling low-value accounts? NEW column = what discovery adds)")
    for lbl, (allc, newc2) in buckets.items():
        print(f"  {lbl:18} {allc:>7,}  | new: {newc2:>6,}")
    print("\n" + "=" * 78)
    print("SAMPLE FULL RECORDS (richest: have both category + tags)")
    print("=" * 78)
    for r in rich:
        print(f"\n  @{r.username}  ({r.name})")
        print(f"    score={r.twitterscore} band={r.band} based_in={r.based_in} joined={r.joined_date}")
        print(f"    categories : {r.categories}")
        print(f"    tags       : {r.tags}")
        print(f"    followers={r.followers:,} smart_followers={r.smart_followers} source={r.source}")
        if r.description:
            print(f"    bio        : {r.description[:90]}")
    print("\n" + "=" * 78)
    print("SAMPLE FOLLOW-EDGES (twitterscore_follows: follower -> followee, by category)")
    print("=" * 78)
    for fee, fer, cat in edge_rows:
        print(f"  {fer}  follows  {fee}   [{cat}]")


if __name__ == "__main__":
    asyncio.run(main())
