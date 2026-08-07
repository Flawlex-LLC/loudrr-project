"""Resolve smart_set members WITHOUT usernames via the loudrr gateway, then exit.

The 98k voter universe was discovered by ID; only ~33.8k rows carry usernames (the ranked
set). This backfills the other ~64k so the TwitterScore category scrape can cover the full
universe (its query = smart_set handles minus ts_scrape_log — new handles are picked up
automatically on the scraper's next run).

Route: gateway /user/info with the NUMERIC ID passed as userName (gateway-side feature,
founder-confirmed 2026-07-05). NEVER api.twitterapi.io direct. The script probes a known
ID first and exits 3 if the gateway feature isn't live yet, so a wrapper can poll-retry.

Resumable by construction: resolved rows get username set and drop out of the universe.
Dead/suspended/renamed IDs stay username-less and are re-attempted next run (cheap 404s).

    DATABASE_URL=<prod> python -m scripts.resolve_handles_prod          # full run
    RESOLVE_LIMIT=200 ... python -m scripts.resolve_handles_prod        # bounded pilot
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys

import httpx

sys.path.insert(0, ".")

from app.core.config import settings  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    stream=sys.stdout)
log = logging.getLogger("resolve_handles")

PROBE_ID = "44196397"          # elonmusk — stable, never suspended
CONCURRENCY = 12               # inside the gateway team's comfort band, below the crawl's
COMMIT_EVERY = 500
CREDITS_PER_CALL = 18          # single-profile tier


def _norm(u: dict) -> tuple[str | None, str | None, int | None, int | None]:
    """(username, display_name, followers, following) from a gateway profile payload."""
    uname = (u.get("userName") or u.get("screen_name") or "").strip().lstrip("@") or None
    name = (u.get("name") or "").strip() or None
    def num(v):
        try:
            return int(v) if v is not None else None
        except (TypeError, ValueError):
            return None
    return uname, name, num(u.get("followers")), num(u.get("following"))


async def _fetch(client: httpx.AsyncClient, uid: str) -> dict | None:
    r = await client.get("/user/info", params={"userName": uid})
    if r.status_code != 200:
        r.raise_for_status()
    data = r.json().get("data")
    # squatter guard: the response must be the account with THIS id — a handle that
    # happens to be all-digits (e.g. @123456) must never be written to a different row
    if data and str(data.get("id") or "") == str(uid):
        return data
    return None


async def main() -> None:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    base = settings.gateway_base_url.rstrip("/") + "/twitter"
    headers = {"x-api-key": settings.loudrr_gateway_api}
    limit = int(os.environ.get("RESOLVE_LIMIT") or 0) or None

    async with httpx.AsyncClient(base_url=base, headers=headers, timeout=25) as probe:
        try:
            hit = await _fetch(probe, PROBE_ID)
        except Exception as e:  # noqa: BLE001
            log.error("gateway probe errored (%s) — treating as not-live", e)
            hit = None
        if hit is None:
            log.warning("gateway /user/info does not resolve IDs yet — exiting 3 (retry later)")
            raise SystemExit(3)
        log.info("gateway by-ID live (probe -> @%s)", hit.get("userName"))

    eng = create_async_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
    async with eng.connect() as c:
        ids = [r[0] for r in (await c.execute(text(
            "select user_id from smart_set where coalesce(username,'')='' "
            "order by score desc nulls last, user_id" + (f" limit {limit}" if limit else "")
        ))).all()]
    log.info("universe: %s unresolved ids", len(ids))

    sem = asyncio.Semaphore(CONCURRENCY)
    resolved: dict[str, tuple] = {}
    missing = calls = 0

    async with httpx.AsyncClient(base_url=base, headers=headers, timeout=25) as client:
        async def one(uid: str) -> None:
            nonlocal missing, calls
            async with sem:
                for attempt in (1, 2, 3):
                    try:
                        u = await _fetch(client, uid)
                        calls += 1
                        break
                    except Exception:  # noqa: BLE001 — transient gateway hiccup
                        if attempt == 3:
                            calls += 1
                            u = None
                        else:
                            await asyncio.sleep(2 * attempt)
                if u is None:
                    missing += 1   # dead/suspended/protected — stays unresolved, honest
                    return
                uname, name, followers, following = _norm(u)
                if uname:
                    resolved[uid] = (uname[:64], (name or "")[:128] or None, followers, following)

        async def flush() -> int:
            if not resolved:
                return 0
            batch = dict(resolved)
            resolved.clear()
            async with eng.begin() as c:
                for uid, (uname, name, followers, following) in batch.items():
                    await c.execute(text(
                        "update smart_set set username=:u, display_name=coalesce(:n, display_name), "
                        "followers_count=coalesce(:fo, followers_count), "
                        "following_count=coalesce(:fi, following_count) where user_id=:id"
                    ), {"u": uname, "n": name, "fo": followers, "fi": following, "id": uid})
            return len(batch)

        written = 0
        for i in range(0, len(ids), COMMIT_EVERY):
            chunk = ids[i:i + COMMIT_EVERY]
            await asyncio.gather(*(one(uid) for uid in chunk))
            written += await flush()
            log.info("progress %s/%s: resolved=%s missing=%s (~$%.2f)",
                     min(i + COMMIT_EVERY, len(ids)), len(ids), written, missing,
                     calls * CREDITS_PER_CALL / settings.gateway_credits_per_usd)

    log.info("DONE: resolved=%s missing=%s calls=%s (~$%.2f)",
             written, missing, calls,
             calls * CREDITS_PER_CALL / settings.gateway_credits_per_usd)


if __name__ == "__main__":
    asyncio.run(main())
