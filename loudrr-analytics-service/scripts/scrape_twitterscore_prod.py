"""Scrape TwitterScore CATEGORIES (+ profile fields) for every tracked account with a handle,
through the Webshare proxy pool, writing to PROD. Server-ready:

  * proxies from env (TS_PROXIES / KAITO_PROXIES inline "h:p:u:pw" lines) or data/proxies file,
  * universe = smart_set handles (~34k with usernames) minus already-scraped,
  * RESUMABLE via a prod ts_scrape_log table (container restarts continue — no local state),
  * finishes by copying categories into ranked_accounts so profiles show them immediately.

Run modes:
  RUN_MODE=twitterscore python -m app.engagement... (Coolify)          # server, internal DB
  python -m scripts.scrape_twitterscore_prod --pilot                   # 50 handles, verify
  python -m scripts.scrape_twitterscore_prod                           # full

Tags NOTE: TwitterScore's direct tag API is premium-gated (key required). An account's tags are
still collectable keylessly via the followedBy snowball (scripts/harvest_twitterscore_followedby)
— run that separately; this job owns CATEGORIES.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
import sys

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

sys.path.insert(0, ".")
from app.clients.twitterscore import TwitterScoreScraper  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.db.models import RankedAccount, SmartSetMember, TwitterScoreAccount  # noqa: E402
from app.db.session import Base, SessionLocal, engine  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    stream=sys.stdout)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("ts_scrape")

_ROW_CHUNK = 60


def _proxies() -> list[str]:
    """Webshare proxies from env (server) or the local file. 'h:p:u:pw' -> http URL."""
    raw = os.environ.get("TS_PROXIES") or settings.kaito_proxies or ""
    lines = [ln.strip() for ln in re.split(r"[\n,;]+", raw) if ln.strip()]
    if not lines and os.path.exists(settings.kaito_proxy_file):
        lines = [ln.strip() for ln in open(settings.kaito_proxy_file, encoding="utf-8") if ln.strip()]
    out = []
    for ln in lines:
        if ln.startswith("http"):
            out.append(ln)
        else:
            p = ln.split(":")
            if len(p) == 4:
                h, port, u, pw = p
                out.append(f"http://{u}:{pw}@{h}:{port}")
    if not out:
        sys.exit("No proxies (set TS_PROXIES) — refusing to scrape from a bare IP.")
    return out


async def _ensure_log() -> None:
    async with engine.begin() as c:
        await c.run_sync(Base.metadata.create_all)
        await c.execute(text("CREATE TABLE IF NOT EXISTS ts_scrape_log "
                             "(handle text PRIMARY KEY, scraped_at timestamp DEFAULT now())"))


async def _universe() -> list[str]:
    """smart_set handles to scrape (DB-resumable across restarts).

    Default: only handles never scraped. With TS_REFRESH_DAYS set, handles whose last scrape
    is older than that are re-scraped too — TwitterScore RECATEGORISES accounts over time, and
    a live spot-check of our 10-day-old data found ~25% had drifted (e.g. @moodlezoup
    Influencers -> VC/Fund Team, @TheFirstMint Projects -> Media). Categories are not static,
    so a one-shot scrape silently rots.
    """
    refresh_days = int(os.environ.get("TS_REFRESH_DAYS") or "0")
    async with SessionLocal() as s:
        handles = [u.lstrip("@") for u in (await s.execute(
            select(SmartSetMember.username).where(SmartSetMember.username.is_not(None)))).scalars()]
        if refresh_days > 0:
            done = {r[0] for r in (await s.execute(text(
                "SELECT handle FROM ts_scrape_log "
                "WHERE scraped_at > now() - make_interval(days => :d)"), {"d": refresh_days})).all()}
        else:
            done = {r[0] for r in (await s.execute(text("SELECT handle FROM ts_scrape_log"))).all()}
    seen, out = set(), []
    for h in handles:
        k = h.lower()
        if k and k not in seen and k not in done:
            seen.add(k); out.append(h)
    return out


async def _upsert(rows: list[dict]) -> None:
    if not rows:
        return
    async with SessionLocal() as s:
        for i in range(0, len(rows), _ROW_CHUNK):
            stmt = pg_insert(TwitterScoreAccount).values(rows[i:i + _ROW_CHUNK])
            ex = stmt.excluded
            stmt = stmt.on_conflict_do_update(
                index_elements=[TwitterScoreAccount.user_id],
                set_={
                    "username": ex.username, "name": ex.name, "twitterscore": ex.twitterscore,
                    "band": ex.band, "followers": ex.followers, "source": ex.source,
                    "description": func.coalesce(ex.description, TwitterScoreAccount.description),
                    "based_in": func.coalesce(ex.based_in, TwitterScoreAccount.based_in),
                    "joined_date": func.coalesce(ex.joined_date, TwitterScoreAccount.joined_date),
                    "renamed_count": func.coalesce(ex.renamed_count, TwitterScoreAccount.renamed_count),
                    "categories": func.coalesce(func.nullif(ex.categories, ""), TwitterScoreAccount.categories),
                })
            await s.execute(stmt)
        await s.commit()


async def _log_handles(handles: list[str]) -> None:
    if not handles:
        return
    async with SessionLocal() as s:
        for i in range(0, len(handles), 200):
            chunk = handles[i:i + 200]
            # DO UPDATE, not DO NOTHING: on a refresh pass the timestamp must move forward,
            # otherwise the handle stays "stale" forever and every run re-scrapes it.
            await s.execute(
                text("INSERT INTO ts_scrape_log (handle) VALUES " +
                     ",".join(f"(:h{j})" for j in range(len(chunk))) +
                     " ON CONFLICT (handle) DO UPDATE SET scraped_at = now()"),
                {f"h{j}": h.lower() for j, h in enumerate(chunk)})
        await s.commit()


async def _sync_categories_to_ranked() -> int:
    """Copy freshly-scraped categories into the public serving table."""
    async with SessionLocal() as s:
        res = await s.execute(text("""
            UPDATE ranked_accounts r SET categories = t.categories
            FROM twitterscore_accounts t
            WHERE t.user_id = r.user_id AND t.categories IS NOT NULL AND t.categories <> ''
              AND (r.categories IS DISTINCT FROM t.categories)"""))
        await s.commit()
        return res.rowcount or 0


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pilot", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--concurrency", type=int, default=40)
    args = ap.parse_args()

    await _ensure_log()
    scraper = TwitterScoreScraper(_proxies())
    handles = await _universe()
    if args.pilot:
        handles = handles[:50]
    elif args.limit:
        handles = handles[:args.limit]
    logger.info("ts scrape: %d handles to go, concurrency=%d", len(handles), args.concurrency)

    found = none = 0
    for ci in range(0, len(handles), args.concurrency):
        chunk = handles[ci:ci + args.concurrency]
        results = await asyncio.gather(*[scraper.fetch_profile(h) for h in chunk])
        rows = []
        for h, d in zip(chunk, results):
            if d and d.get("user_id"):
                rows.append({"user_id": d["user_id"], "username": d.get("username") or h,
                             "name": d.get("name"), "twitterscore": d.get("twitterscore"),
                             "band": d.get("band"), "followers": d.get("followers"),
                             "description": d.get("description"),
                             "categories": d.get("categories") or "",
                             "based_in": d.get("based_in"), "joined_date": d.get("joined_date"),
                             "renamed_count": d.get("renamed_count"), "source": "profile"})
                found += 1
            else:
                none += 1
        await _upsert(rows)
        await _log_handles(chunk)              # log AFTER upsert -> a crash re-does the batch
        if (ci // args.concurrency) % 5 == 0:
            logger.info("  %d/%d (found=%d none/404=%d)", min(ci + args.concurrency, len(handles)),
                        len(handles), found, none)

    synced = await _sync_categories_to_ranked()
    async with SessionLocal() as s:
        total = (await s.execute(select(func.count()).select_from(TwitterScoreAccount))).scalar()
        with_cat = (await s.execute(select(func.count()).select_from(TwitterScoreAccount)
                    .where(TwitterScoreAccount.categories.is_not(None),
                           TwitterScoreAccount.categories != ""))).scalar()
    logger.info("DONE: scraped=%d found=%d none=%d | twitterscore_accounts=%d (with categories=%d) "
                "| ranked_accounts categories synced=%d", len(handles), found, none, total, with_cat, synced)


if __name__ == "__main__":
    asyncio.run(main())
