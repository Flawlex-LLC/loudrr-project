"""Harvest TweetScout/Sorsa ground truth, then (optionally) bootstrap M + fit parity.

Requires a FUNDED Sorsa key (the one in .env was out of quota 2026-06-14). SPENDS
Sorsa quota (~3 req/account). See docs/parity_strategy.md.

    # pull their data for our KOL list, then bootstrap their M into smart_set and fit
    python -m scripts.harvest_groundtruth \
        --csv ../scraper-twitter/AnkhLabs_KOLs_1.5K_enriched.csv --bootstrap --fit

    python -m scripts.harvest_groundtruth --usernames cz_binance,VitalikButerin
"""
import argparse
import asyncio

from app.services.calibration import bootstrap_smart_set_from_harvest, fit, harvest
from app.services.seed import handles_from_csv


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--usernames", type=str, default="", help="comma-separated handles")
    ap.add_argument("--csv", type=str, default="", help="CSV of accounts to harvest")
    ap.add_argument("--csv-link-col", type=int, default=1)
    ap.add_argument("--csv-skip-rows", type=int, default=2)
    ap.add_argument("--with-following", action="store_true", help="also pull /top-following")
    ap.add_argument("--bootstrap", action="store_true", help="promote harvested M into smart_set")
    ap.add_argument("--fit", action="store_true", help="fit isotonic calibration + report parity")
    args = ap.parse_args()

    handles: list[str] = []
    if args.csv:
        handles += handles_from_csv(args.csv, link_col=args.csv_link_col, skip_rows=args.csv_skip_rows)
    if args.usernames:
        handles += [h.strip() for h in args.usernames.split(",") if h.strip()]
    if not handles:
        ap.error("provide --usernames and/or --csv")

    print(await harvest(handles, with_following=args.with_following))
    if args.bootstrap:
        print("bootstrapped:", await bootstrap_smart_set_from_harvest())
    if args.fit:
        print(await fit())


if __name__ == "__main__":
    asyncio.run(main())
