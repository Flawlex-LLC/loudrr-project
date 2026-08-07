"""Recompute member scores (personalized PageRank). FREE — pure compute, no API.

    python -m scripts.run_score

Run after each crawl. NetworkX runs over the M-internal subgraph (small), so this
is seconds-to-minutes even at 50k members.
"""
import asyncio

from app.services.score import recompute_member_scores


async def main() -> None:
    print(await recompute_member_scores())


if __name__ == "__main__":
    asyncio.run(main())
