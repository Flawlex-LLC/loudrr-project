"""Run the following-graph crawl. SPENDS wallet credits on the loudrr gateway
(drop-in twitterapi.io). This is the main data-collection cost.

    python -m scripts.run_crawl --pilot                       # small, measured pilot
    python -m scripts.run_crawl --limit 100 --budget-credits 200000
    python -m scripts.run_crawl --budget 200                  # full run, ~$200 cap

--pilot crawls a few members under a hard member-limit + credit safety cap and prints
the MEASURED unit cost (wallet credit delta) plus a projection for the full smart_set,
so cost is read off the live wallet — never assumed. The budget guard is credit-native
(exact); --budget (USD) is converted at the provider's rate. Stops cleanly at budget /
empty-queue / quota; re-running resumes (skips already-crawled members), so a full run
is just repeated bounded runs. See docs/cost_model.md.
"""
import argparse
import asyncio
import logging
import os
import sys

from sqlalchemy import func, select

from app.services.crawl import crawl
from app.db.models import SmartSetMember
from app.db.session import SessionLocal

# Logs go to STDOUT (not the default stderr): under PowerShell 5.1 a native exe's
# stderr is wrapped as a NativeCommandError and can abort the process, so progress
# logs on stderr would kill the crawl. Quiet httpx's per-request chatter too.
# ALSO tee everything to logs/crawl.log so progress is watchable in real time
# (PowerShell: Get-Content logs\crawl.log -Wait -Tail 40).
os.makedirs("logs", exist_ok=True)
_file = logging.FileHandler(os.path.join("logs", "crawl.log"), encoding="utf-8")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout), _file])
logging.getLogger("httpx").setLevel(logging.WARNING)


async def _member_total() -> int:
    async with SessionLocal() as s:
        return (await s.execute(select(func.count()).select_from(SmartSetMember))).scalar() or 0


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pilot", action="store_true",
                    help="small measured pilot (default --limit 10, ~100k-credit safety cap)")
    ap.add_argument("--sample", type=int, default=None,
                    help="crawl N RANDOM uncrawled members (unbiased cost measurement); implies --pilot")
    ap.add_argument("--limit", type=int, default=None, help="max members this run (hard cap)")
    ap.add_argument("--budget", type=float, default=None, help="USD budget guard")
    ap.add_argument("--budget-credits", type=float, default=None, dest="budget_credits",
                    help="credit budget guard (exact; wins over --budget)")
    ap.add_argument("--concurrency", type=int, default=None,
                    help="members crawled in parallel (default settings.crawl_concurrency)")
    args = ap.parse_args()

    pilot = args.pilot or args.sample is not None
    random_order = args.sample is not None
    limit = args.sample if args.sample is not None else (
        args.limit if args.limit is not None else (10 if args.pilot else None))
    budget_credits = args.budget_credits
    budget_usd = args.budget
    if pilot and budget_credits is None and budget_usd is None:
        budget_credits = 1_000_000  # generous backstop; the --limit/--sample binds first

    summary = await crawl(limit=limit, budget_credits=budget_credits, budget_usd=budget_usd,
                          random_order=random_order, concurrency=args.concurrency)

    print("\n=== crawl summary ===")
    for k, v in summary.items():
        print(f"  {k:22} {v}")

    # sanity check the review flagged: if failures parked members, the projection is unreliable
    if pilot and limit and summary["members_crawled"] < limit:
        print(f"\n  ! only {summary['members_crawled']}/{limit} members completed "
              f"(gone={summary['accounts_gone_404']}, failed={summary['failed_will_retry']}) "
              f"— projection below is approximate")

    cpm = summary.get("credits_per_member")
    if pilot and cpm:
        total = await _member_total()
        members = summary["members_crawled"] or 1
        edges = summary["edges_written"] or 0
        usd_per = (summary["usd_measured"] / members) if summary.get("usd_measured") else None
        print("\n  MEASURED unit cost (off the live wallet — credits are authoritative):")
        print(f"    {cpm:,.0f} credits / member   ({edges / members:,.0f} following/member avg)"
              + (f"   ~${usd_per:.4f}/member*" if usd_per is not None else ""))
        print(f"\n  PROJECTED full crawl of {total:,} members:")
        full_credits = cpm * total
        line = f"    ~{full_credits:,.0f} credits"
        if usd_per is not None:
            line += f"  (~${usd_per * total:,.2f}*)"
        print(line)
        print("    -> compare to wallet balance before launching --full")
        if usd_per is not None:
            print("    * USD is approximate (gateway credit<->USD rate being aligned to "
                  "twitterapi.io); credits are exact")


if __name__ == "__main__":
    asyncio.run(main())
