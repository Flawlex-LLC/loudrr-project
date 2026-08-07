"""Crawl status / cost monitor — read-only, spends nothing meaningful (one balance call).

    python -m scripts.crawl_status

Reports gateway cumulative spend (total_spent / total_calls — recharge-immune, unlike
the balance which moves when you top up) and DB progress (edges, members crawled), plus
derived unit costs and a full-crawl projection. Use this to watch a long crawl instead of
trusting shell exit codes (which misreport on Windows backgrounding).
"""
import asyncio

from sqlalchemy import select, func

from app.db.session import SessionLocal
from app.db.models import Edge, SmartSetMember
from app.clients.twitterapi import TwitterAPIClient

FULL_TARGET = 7382  # current smart_set size


async def main() -> None:
    tw = TwitterAPIClient()
    bal = await tw.get_balance()
    async with SessionLocal() as s:
        edges = (await s.execute(select(func.count()).select_from(Edge))).scalar()
        crawled = (await s.execute(
            select(func.count()).select_from(SmartSetMember)
            .where(SmartSetMember.last_crawled_at.isnot(None)))).scalar()
        total = (await s.execute(select(func.count()).select_from(SmartSetMember))).scalar()
    spent = bal.get("total_spent") or 0
    calls = bal.get("total_calls") or 0
    print(f"provider          loudrr-gateway")
    print(f"wallet balance    {bal.get('recharge_credits'):,} credits")
    print(f"cumulative spend  {spent:,} credits over {calls:,} calls")
    print(f"DB progress       {crawled:,}/{total:,} members crawled, {edges:,} edges")
    if crawled:
        cpm = spent / crawled
        print(f"unit cost         {cpm:,.0f} credits/member | {spent/max(edges,1):.3f} credits/edge "
              f"| {edges/crawled:,.0f} edges/member | {spent/max(calls,1):.1f} credits/call")
        print(f"projection        full {FULL_TARGET:,} members ~= {cpm*FULL_TARGET:,.0f} credits "
              f"(NOTE: biased high if only mega-seeds crawled so far)")


if __name__ == "__main__":
    asyncio.run(main())
