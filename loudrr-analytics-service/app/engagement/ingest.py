"""Bronze ingest: each smart-set member's COMPLETE outbound stream (replies + native RTs + quotes
+ originals).

    python -m app.engagement.service --max-accounts 50 --max-pages 2 --budget 1   # pilot
    python -m app.engagement.service                                               # full pass

Source is configurable via ``settings.engagement_source`` — both capture the same outbound edges
now that the gateway fixed last_tweets (verified 2026-07-23: deep pagination + 80-100% reply capture
+ RT/quote parity, identical field names so ``extract_edges`` is unchanged):
  * ``advanced_search`` (DEFAULT) — ``from:<user> include:nativeretweets``; 0% foreign overhead,
    cheaper; guards handle squat/rename via the author.id check.
  * ``timeline`` — ``/user/last_tweets`` by NUMERIC id, includeReplies, paged by the TOP-LEVEL
    next_cursor; rename-immune, but includeReplies pads the stream with thread-context tweets by
    OTHERS (dropped by the author guard, but still billed -> ~1.75x cost).

Dedicated system — deliberately does NOT share pacing, budget, or tables with the follower
crawl: its own TwitterAPIClient (engagement_rate_calls/min, below the crawl's band) and its own
budget ceiling, so a runaway here can never starve or break follower scoring.

Members are processed in WAVES with a DB flush per wave (bounded memory at thousands of members;
cursor advances only after its wave's tweets are committed -> crash-safe resume).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select

from app.core.config import settings
from app.db.models import SmartSetMember
from app.db.session import Base, SessionLocal, engine
from app.engagement.extract import _parse_dt, extract_edges
from app.engagement.models import EngCursor, EngTweetRaw

logger = logging.getLogger("eng.ingest")

# Extract edges/calls every N waves DURING ingest — the first 20k pass takes ~2 days, and
# waiting until it finishes to extract left heatmaps/KOL-signals empty for days. This keeps
# derived data flowing continuously as tweets land.
_EXTRACT_EVERY_WAVES = 20


def _to_raw(t: dict, member_id: str) -> EngTweetRaw | None:
    tid = str(t.get("id") or "")
    if not tid:
        return None
    return EngTweetRaw(
        tweet_id=tid,
        member_id=str(member_id),
        created_at=_parse_dt(t.get("createdAt")),
        text=(t.get("text") or "")[:4096],
        raw=t,
    )


async def _universe(max_accounts: int | None) -> list[tuple[str, str | None]]:
    """(member_id, username) to poll, highest PageRank first.

    ENGAGEMENT_UNIVERSE (locked with founder 2026-07-02):
      * "seeds"    — the ~3,894 hand-curated anchors (validation / default)
      * "top:N"    — seeds UNION top-N members by PageRank score (prod target: top:20000 —
                     at rank 20k the median account still has ~106 elite followers, so the
                     "smart" label holds; beyond ~25-30k it doesn't)
      * "all"      — full smart_set (~98k). Deliberate separate spend decision, never default.
    """
    uni = (settings.engagement_universe or "seeds").strip().lower()
    cols = (SmartSetMember.user_id, SmartSetMember.username)
    order = (SmartSetMember.score.desc().nulls_last(), SmartSetMember.user_id)
    async with SessionLocal() as s:
        if uni.startswith("top:"):
            n = int(uni.split(":", 1)[1])
            top = list((await s.execute(select(*cols).order_by(*order).limit(n))).all())
            seen = {uid for uid, _ in top}
            seeds = list((await s.execute(
                select(*cols).where(SmartSetMember.is_seed.is_(True)).order_by(*order))).all())
            members = top + [row for row in seeds if row[0] not in seen]
        elif uni == "all":
            members = list((await s.execute(select(*cols).order_by(*order))).all())
        else:  # seeds (default)
            members = list((await s.execute(
                select(*cols).where(SmartSetMember.is_seed.is_(True)).order_by(*order))).all())
    members = [(str(uid), (uname or "").strip().lstrip("@") or None) for uid, uname in members]
    return members[:max_accounts] if max_accounts else members


async def ingest_members(*, max_pages: int | None = None, max_accounts: int | None = None,
                         concurrency: int | None = None, budget_usd: float | None = None,
                         client=None, flush_every: int = 50) -> dict:
    max_pages = max_pages or settings.engagement_max_pages
    concurrency = concurrency or settings.engagement_concurrency
    if budget_usd is None:
        budget_usd = settings.engagement_daily_budget_usd

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    members = await _universe(max_accounts)
    async with SessionLocal() as s:
        cursors = {c.member_id: c.since_id
                   for c in (await s.execute(select(EngCursor))).scalars().all()}

    if client is None:
        from app.clients.twitterapi import TwitterAPIClient
        client = TwitterAPIClient(rate_calls=settings.engagement_rate_calls,
                                  rate_window_s=settings.engagement_rate_window_s)

    def over_budget() -> bool:
        return budget_usd is not None and getattr(client, "usd_spent", 0.0) >= budget_usd

    sem = asyncio.Semaphore(concurrency)
    tweets_fetched = tweets_inserted = 0
    skipped_no_username = sum(1 for _, uname in members if not uname)
    budget_stopped = False
    # Incremental pulls page DEEPER than first pulls: with a cursor set, we must reach
    # since_id or we leave a permanent gap (newest-first + cursor advance = the gap is
    # never revisited). Hard cap bounds a pathological poster; if even that truncates,
    # we accept the bounded loss LOUDLY (warning) rather than stall the member forever.
    incremental_max_pages = max(max_pages, 10)

    async def pull(member_id: str, username: str) -> tuple[str, list[EngTweetRaw], str | None] | None:
        nonlocal budget_stopped
        async with sem:
            if over_budget():
                budget_stopped = True
                return None
            since = cursors.get(member_id)
            rows: list[EngTweetRaw] = []
            newest = since
            try:
                pages = incremental_max_pages if since else max_pages
                # Both sources capture the same outbound edges (replies + native RTs + quotes +
                # originals) since the gateway fixed last_tweets; advanced_search is cheaper (0%
                # foreign overhead), timeline is rename-immune (~1.75x cost). Configurable.
                if settings.engagement_source == "timeline":
                    stream = client.iter_user_tweets(user_id=member_id, since_id=since,
                                                     max_pages=pages, include_replies=True)
                else:  # advanced_search (default)
                    stream = client.iter_search_tweets(
                        query=f"from:{username} include:nativeretweets",
                        since_id=since, max_pages=pages)
                async for t in stream:
                    r = _to_raw(t, member_id)
                    if r is None:
                        continue
                    # never store or let a cursor advance on a tweet not authored by THIS member
                    # id — guards handle squat/rename (search) + thread-context padding (timeline).
                    author_id = str((t.get("author") or {}).get("id") or "")
                    if author_id and author_id != str(member_id):
                        continue
                    rows.append(r)
                    if r.tweet_id.isdigit() and int(r.tweet_id) > int(newest or 0):
                        newest = r.tweet_id
            except Exception as e:  # noqa: BLE001 - one bad member must not kill the run
                # Partial fetch: keep the rows (id-dedup absorbs the refetch) but do NOT
                # advance the cursor — advancing past unfetched territory silently loses
                # every tweet between the old cursor and the oldest fetched one.
                logger.warning("ingest %s (@%s): %s — cursor NOT advanced", member_id, username, e)
                return (member_id, rows, since)
            if since and rows:
                oldest = min((int(r.tweet_id) for r in rows if r.tweet_id.isdigit()), default=0)
                if oldest > int(since):
                    # paged out before reaching the old cursor: bounded loss, say so loudly
                    logger.warning("ingest %s (@%s): gap between %s and %s after %s pages "
                                   "(prolific poster) — some tweets skipped",
                                   member_id, username, since, oldest, pages)
            return (member_id, rows, newest)

    for w in range(0, len(members), flush_every):
        if over_budget():
            budget_stopped = True
            break
        wave = [(m, u) for m, u in members[w:w + flush_every] if u]
        results = [r for r in await asyncio.gather(*(pull(m, u) for m, u in wave))
                   if r is not None]

        # flush the wave: dedup in-batch + against DB, insert, advance cursors — one commit
        by_id: dict[str, EngTweetRaw] = {}
        for _, rows, _ in results:
            for r in rows:
                by_id.setdefault(r.tweet_id, r)
        async with SessionLocal() as s:
            existing: set[str] = set()
            ids = list(by_id)
            for i in range(0, len(ids), 400):
                existing |= set((await s.execute(
                    select(EngTweetRaw.tweet_id)
                    .where(EngTweetRaw.tweet_id.in_(ids[i:i + 400])))).scalars().all())
            fresh = [r for tid, r in by_id.items() if tid not in existing]
            s.add_all(fresh)

            now = datetime.now(timezone.utc).replace(tzinfo=None)
            for member_id, rows, newest in results:
                cur = await s.get(EngCursor, member_id)
                if cur is None:
                    s.add(EngCursor(member_id=member_id, since_id=newest, last_run_at=now,
                                    tweets_seen=len(rows)))
                else:
                    cur.since_id = newest or cur.since_id
                    cur.last_run_at = now
                    cur.tweets_seen = (cur.tweets_seen or 0) + len(rows)
            await s.commit()

        tweets_fetched += sum(len(r) for _, r, _ in results)
        tweets_inserted += len(fresh)
        wave_no = w // flush_every + 1
        logger.info("wave %s/%s: +%s tweets (usd_spent=%.4f)",
                    wave_no, (len(members) + flush_every - 1) // flush_every,
                    len(fresh), getattr(client, "usd_spent", 0.0))

        # keep ALL derived data flowing during the multi-day first pass — edges (heatmaps),
        # calls (KOL Signals), and token enrichment — so nothing sits empty for days.
        # Idempotent; a failure here must never abort the paid ingest.
        if wave_no % _EXTRACT_EVERY_WAVES == 0:
            try:
                from app.engagement.calls import extract_calls
                from app.engagement.onchain import enrich_calls
                ex = await extract_edges()
                ca = await extract_calls()
                en = await enrich_calls(limit=300)
                logger.info("  interim: +%s edges, +%s calls, %s tokens resolved",
                            ex.get("edges_inserted", 0), ca.get("calls_inserted", 0),
                            en.get("resolved", 0))
            except Exception:  # noqa: BLE001
                logger.exception("interim extract failed (continuing ingest)")

    return {
        "accounts": len(members),
        "skipped_no_username": skipped_no_username,
        "tweets_fetched": tweets_fetched,
        "tweets_inserted": tweets_inserted,
        "budget_stopped": budget_stopped,
        "credits_spent": round(getattr(client, "credits_spent", 0.0), 1),
        "usd_spent": round(getattr(client, "usd_spent", 0.0), 4),
    }
