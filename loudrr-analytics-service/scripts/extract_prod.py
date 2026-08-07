"""One-off: run the engagement EXTRACT stages against PROD over the public port, to turn the
already-ingested eng_tweet_raw into edges / calls / tokens NOW (the worker only extracts after
its full ingest pass finishes). Idempotent + resumable (parsed / calls_parsed watermarks).

    python -m scripts.extract_prod
"""
import asyncio
import os
import sys

from dotenv import dotenv_values

sys.path.insert(0, ".")
_pw = dotenv_values(".env")["LOUDRR_PG_PASSWORD"]
# bind the app engine to PROD before importing anything that reads DATABASE_URL
os.environ["DATABASE_URL"] = (
    f"postgresql+asyncpg://postgres:{_pw}@213.199.54.248:5433/loudrr_analytics")

from app.db.session import engine  # noqa: E402
from app.engagement.calls import extract_calls  # noqa: E402
from app.engagement.extract import extract_edges  # noqa: E402
from app.engagement.onchain import enrich_calls, refresh_tokens  # noqa: E402


async def _until_done(fn, name):
    """Run a resumable stage to completion over the flaky proxy: on any connection drop,
    dispose stale pool connections and retry — the stage continues from its DB watermark."""
    for attempt in range(200):
        try:
            r = await fn()
            print(f"{name}: {r}", flush=True)
            return r
        except Exception as e:  # noqa: BLE001
            print(f"  {name} drop ({type(e).__name__}) — retry {attempt+1} from watermark", flush=True)
            try:
                await engine.dispose()
            except Exception:  # noqa: BLE001
                pass
            await asyncio.sleep(3)
    print(f"{name}: gave up after 200 retries", flush=True)


async def main() -> None:
    await _until_done(extract_edges, "EDGES")
    await _until_done(extract_calls, "CALLS")
    total = 0
    for _ in range(80):
        try:
            r = await enrich_calls(limit=500)
        except Exception:  # noqa: BLE001
            await engine.dispose(); await asyncio.sleep(3); continue
        total += r["resolved"]
        if r["examined"] < 500:
            break
    print(f"ENRICHED: {total} calls resolved", flush=True)
    await _until_done(refresh_tokens, "REFRESH")


if __name__ == "__main__":
    asyncio.run(main())
