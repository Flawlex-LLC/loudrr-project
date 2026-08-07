"""One-shot: finish KOL-call extraction + resolve every call's token (DexScreener), then exit.

Runs on the SERVER against the INTERNAL prod DB (no proxy on the DB side) so the DexScreener
loop can't drop the DB connection. DexScreener itself is reached through the rotating Webshare
pool (env ONCHAIN_PROXIES) because its Cloudflare blocks our datacenter IP — without that,
every lookup fails and 0 tokens resolve. Idempotent + resumable. Populates eng_call + eng_token
-> KOL Signals.
"""
import asyncio
import logging
import sys

sys.path.insert(0, ".")
from app.db.session import engine
from app.engagement.calls import extract_calls
from app.engagement.onchain import _proxy_urls, enrich_calls, refresh_tokens

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    stream=sys.stdout)
log = logging.getLogger("enrich_prod")

LIMIT = 400
MAX_PASSES = 500       # hard backstop (covers ~200k unresolved calls per run)


async def main():
    log.info("proxies configured: %s", len(_proxy_urls()))
    log.info("extract_calls: %s", await extract_calls())

    # Full backlog sweep, newest -> oldest by keyset (max_id): every unresolved call is
    # examined exactly ONCE per run. Without the cursor, permanently-unresolvable calls
    # (native cashtags, stock tickers) clog the newest-N window and the sweep stalls there.
    total = 0
    max_id = None
    for i in range(MAX_PASSES):
        try:
            r = await enrich_calls(limit=LIMIT, max_id=max_id)
        except Exception as e:  # noqa: BLE001 — transient DB/network hiccup: reset + retry
            log.warning("pass %s errored (%s: %.160s) — disposing engine, retrying",
                        i, type(e).__name__, e)
            await engine.dispose()
            await asyncio.sleep(3)
            continue  # max_id unchanged -> the same block is retried, nothing skipped
        total += r["resolved"]
        if i % 3 == 0 or r["resolved"]:
            log.info("pass %s: +%s (total %s), examined %s, cursor %s",
                     i, r["resolved"], total, r["examined"], r["min_id"])
        if r["examined"] < LIMIT:
            break
        max_id = r["min_id"]

    log.info("ENRICH DONE: %s tokens resolved", total)
    try:
        log.info("refresh: %s", await refresh_tokens())
    except Exception as e:  # noqa: BLE001
        log.warning("refresh skipped: %s", type(e).__name__)


if __name__ == "__main__":
    asyncio.run(main())
