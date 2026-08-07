"""Fetch crypto PROJECT X handles from CoinGecko to open the project/VC neighborhoods
the KOL-seeded harvest under-covers. FREE (CoinGecko API), no Sorsa/twitterapi cost.

Targets the TOP coins by market cap (the projects that actually carry influence) — NOT
the ~15k dead-token tail (those are exactly the low-score accounts we want to avoid).

    python -m scripts.seed_coingecko --top 1500

Resumable + polite (CoinGecko free tier is rate-limited; backs off on 429). Writes unique
handles to data/coingecko_handles.txt; run_harvest folds them into the seed set.
"""
import argparse
import asyncio
import os

import httpx

from app.core.config import settings

CG = "https://api.coingecko.com/api/v3"
# Demo key -> public base + x-cg-demo-api-key header => ~30 calls/min (vs ~5-15 throttled).
# Neutral browser UA — never identify ourselves (we're a competitor doing research).
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"}
if settings.coingecko_demo:
    _HEADERS["x-cg-demo-api-key"] = settings.coingecko_demo
OUT = os.path.join("data", "coingecko_handles.txt")
DONE = os.path.join("data", "coingecko_done.txt")


def _load(path: str) -> set[str]:
    return {ln.strip() for ln in open(path, encoding="utf-8")} if os.path.exists(path) else set()


async def top_coin_ids(client: httpx.AsyncClient, top_n: int) -> list[str]:
    ids: list[str] = []
    for page in range(1, (top_n // 250) + 2):
        r = await client.get(f"{CG}/coins/markets", params={
            "vs_currency": "usd", "order": "market_cap_desc", "per_page": 250, "page": page})
        if r.status_code != 200:
            await asyncio.sleep(20); continue
        batch = r.json()
        if not batch:
            break
        ids += [c["id"] for c in batch]
        if len(ids) >= top_n:
            break
        await asyncio.sleep(2.5)
    return ids[:top_n]


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=1500, help="top N coins by market cap")
    ap.add_argument("--delay", type=float, default=2.2, help="seconds between detail calls")
    args = ap.parse_args()
    os.makedirs("data", exist_ok=True)
    print(f"coingecko demo key: {'set (~30/min)' if settings.coingecko_demo else 'NONE (throttled)'}")

    done = _load(DONE)
    handles = _load(OUT)
    async with httpx.AsyncClient(timeout=30.0, headers=_HEADERS) as client:
        ids = await top_coin_ids(client, args.top)
        print(f"top {len(ids)} coins; {len(done)} already processed")
        hf = open(OUT, "a", encoding="utf-8")
        df = open(DONE, "a", encoding="utf-8")
        try:
            for i, cid in enumerate(ids):
                if cid in done:
                    continue
                try:
                    r = await client.get(f"{CG}/coins/{cid}", params={
                        "localization": "false", "tickers": "false", "market_data": "false",
                        "community_data": "false", "developer_data": "false", "sparkline": "false"})
                except httpx.HTTPError:
                    await asyncio.sleep(args.delay); continue
                if r.status_code == 429:           # rate-limited -> back off hard, retry later
                    print(f"  429 at {cid} -> backing off 30s"); await asyncio.sleep(30); continue
                if r.status_code == 200:
                    h = (r.json().get("links", {}) or {}).get("twitter_screen_name")
                    if h and h.strip():
                        h = h.strip().lstrip("@")
                        if h.lower() not in {x.lower() for x in handles}:
                            handles.add(h); hf.write(h + "\n"); hf.flush()
                    df.write(cid + "\n"); df.flush(); done.add(cid)
                if (i + 1) % 100 == 0:
                    print(f"  {i+1}/{len(ids)} coins -> {len(handles)} handles")
                await asyncio.sleep(args.delay)
        finally:
            hf.close(); df.close()
    print(f"done -> {OUT}: {len(handles)} unique project handles")


if __name__ == "__main__":
    asyncio.run(main())
