"""Seed the smart set. SPENDS on twitterapi.io (profile resolution, ~$0.0002/handle).

    python -m scripts.run_seed --coingecko 2000
    python -m scripts.run_seed --handles a16z,paradigm,cz_binance --category vc

CoinGecko projects come pre-labeled PROJECT. Other handle lists get the bio/name
heuristic unless --category is passed. The crawl + score jobs expand this seed into
the full 50k+ smart set automatically (research §9).
"""
import argparse
import asyncio

from app.db.models import Category
from app.services.seed import add_handles, coingecko_handles, handles_from_csv


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--coingecko", type=int, default=0, help="import N project handles")
    ap.add_argument("--handles", type=str, default="", help="comma-separated handles")
    ap.add_argument("--csv", type=str, default="", help="path to a CSV of accounts")
    ap.add_argument("--csv-link-col", type=int, default=1, help="0-based X-link column")
    ap.add_argument("--csv-skip-rows", type=int, default=2, help="header rows to skip")
    ap.add_argument("--category", type=str, default="", help="force category for --handles/--csv")
    args = ap.parse_args()
    cat = Category(args.category) if args.category else None

    if args.coingecko:
        handles = await coingecko_handles(limit=args.coingecko)
        await add_handles(handles, source="coingecko", category=Category.PROJECT)

    if args.csv:
        handles = handles_from_csv(
            args.csv, link_col=args.csv_link_col, skip_rows=args.csv_skip_rows
        )
        # KOL sheets are predominantly influencer/trader accounts -> default INFLUENCER
        await add_handles(handles, source="csv", category=cat or Category.INFLUENCER)

    if args.handles:
        await add_handles(
            [h.strip() for h in args.handles.split(",") if h.strip()],
            source="manual", category=cat,
        )


if __name__ == "__main__":
    asyncio.run(main())
