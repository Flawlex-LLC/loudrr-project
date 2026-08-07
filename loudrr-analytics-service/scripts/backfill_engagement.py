"""One-shot HISTORICAL timeline backfill: extend every panel member's fetched window back to
the full heatmap year (founder-approved 2026-07-07, ~$4.9k budget).

Forward ingest fetches newest-first from tracking start and only moves forward, so a
year-ago day is only covered by ~35% of the panel. This walks each member's timeline
BACKWARD from their oldest fetched tweet to the cutoff using the ``max_id:`` search
operator (pilot-verified through the gateway 2026-07-07; boundary is off-by-one INCLUSIVE,
which the tweet_id PK dedup absorbs).

Safety properties:
  * budget is CRASH-SAFE: remaining = BACKFILL_TOTAL_BUDGET_USD - SUM(eng_backfill.credits),
    so a restart-looping container resumes accounting, never re-spends from zero;
  * resumable at member granularity (done rows skipped) AND within a member (oldest fetched
    tweet is recomputed from eng_tweet_raw each pass);
  * never touches eng_cursor — the forward watermark stays the daily worker's alone;
  * highest-score members first, so a partial budget buys the most valuable coverage;
  * interim edge/call extraction keeps heatmaps + KOL signals improving mid-run.

Env: BACKFILL_TOTAL_BUDGET_USD=5100  BACKFILL_RATE_CALLS=240  BACKFILL_CONCURRENCY=16
     BACKFILL_WINDOW_DAYS=364  BACKFILL_MEMBER_PAGE_CAP=800
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, ".")

from sqlalchemy import Numeric, cast, func, select  # noqa: E402

from app.db.models import SmartSetMember  # noqa: E402
from app.db.session import Base, SessionLocal, engine  # noqa: E402
from app.engagement.extract import _parse_dt  # noqa: E402
from app.engagement.models import EngBackfill, EngTweetRaw  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    stream=sys.stdout)
logger = logging.getLogger("eng.backfill")

TOTAL_BUDGET_USD = float(os.environ.get("BACKFILL_TOTAL_BUDGET_USD") or "5100")
RATE_CALLS = int(os.environ.get("BACKFILL_RATE_CALLS") or "240")          # per minute
CONCURRENCY = int(os.environ.get("BACKFILL_CONCURRENCY") or "16")
WINDOW_DAYS = int(os.environ.get("BACKFILL_WINDOW_DAYS") or "364")
MEMBER_PAGE_CAP = int(os.environ.get("BACKFILL_MEMBER_PAGE_CAP") or "800")  # ~16k tweets ≈ $24
CHUNK_PAGES = 25                       # pages per max_id anchor before re-anchoring
EXTRACT_EVERY_MEMBERS = 150
FLUSH_EVERY_ROWS = 400


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def _universe(cutoff: datetime) -> list[tuple[str, str, str, datetime]]:
    """(member_id, username, oldest_tweet_id, oldest_created_at) for members whose fetched
    window does NOT reach the cutoff yet, richest (highest-score) first."""
    async with SessionLocal() as s:
        done = set((await s.execute(
            select(EngBackfill.member_id).where(EngBackfill.done.is_(True)))).scalars().all())
        rows = (await s.execute(
            select(EngTweetRaw.member_id,
                   func.min(EngTweetRaw.created_at).label("old"))
            .where(EngTweetRaw.created_at.is_not(None))
            .group_by(EngTweetRaw.member_id)
            .having(func.min(EngTweetRaw.created_at) > cutoff)
        )).all()
        need = {str(m): old for m, old in rows if str(m) not in done}
        if not need:
            return []
        members = (await s.execute(
            select(SmartSetMember.user_id, SmartSetMember.username, SmartSetMember.score)
            .where(SmartSetMember.user_id.in_(list(need)))
        )).all()
        # oldest tweet ID per member — snowflake ids are time-ordered, so the row holding
        # min(created_at) is found via a per-member min over the numeric id
        oldest_ids: dict[str, str] = {}
        for i in range(0, len(list(need)), 500):
            chunk = list(need)[i:i + 500]
            for m, tid in (await s.execute(
                select(EngTweetRaw.member_id, func.min(cast(EngTweetRaw.tweet_id, Numeric)))
                .where(EngTweetRaw.member_id.in_(chunk))
                .group_by(EngTweetRaw.member_id)
            )).all():
                oldest_ids[str(m)] = str(int(tid))
    out = []
    for uid, uname, score in members:
        uid = str(uid)
        uname = (uname or "").strip().lstrip("@")
        if uname and uid in oldest_ids:
            out.append((uid, uname, oldest_ids[uid], need[uid], float(score or 0)))
    out.sort(key=lambda r: -r[4])
    return [(a, b, c, d) for a, b, c, d, _ in out]


async def _spent_prior() -> float:
    async with SessionLocal() as s:
        total = (await s.execute(select(func.coalesce(func.sum(EngBackfill.credits), 0.0)))).scalar()
    return float(total or 0.0) / 100_000.0


async def _flush(rows: dict[str, EngTweetRaw]) -> int:
    if not rows:
        return 0
    async with SessionLocal() as s:
        ids = list(rows)
        existing: set[str] = set()
        for i in range(0, len(ids), 400):
            existing |= set((await s.execute(
                select(EngTweetRaw.tweet_id)
                .where(EngTweetRaw.tweet_id.in_(ids[i:i + 400])))).scalars().all())
        fresh = [r for tid, r in rows.items() if tid not in existing]
        s.add_all(fresh)
        await s.commit()
    return len(fresh)


async def _mark(member_id: str, *, tweets: int, credits: float,
                reached: datetime | None, done: bool) -> None:
    async with SessionLocal() as s:
        row = await s.get(EngBackfill, member_id)
        if row is None:
            row = EngBackfill(member_id=member_id)
            s.add(row)
        row.tweets = (row.tweets or 0) + tweets
        row.credits = (row.credits or 0.0) + credits
        row.reached = reached or row.reached
        row.done = done
        row.updated_at = _utcnow()
        await s.commit()


async def backfill_member(client, member_id: str, username: str,
                          oldest_id: str, oldest_at: datetime, cutoff: datetime) -> dict:
    """Walk one member's timeline backward from their oldest fetched tweet to cutoff."""
    inserted = pages_used = 0
    credits_before = client.credits_spent
    anchor = int(oldest_id)
    reached = oldest_at
    done_reason = "cutoff"
    while pages_used < MEMBER_PAGE_CAP:
        got_any = False
        min_id, min_dt = anchor, reached
        batch: dict[str, EngTweetRaw] = {}
        query = f"from:{username} include:nativeretweets max_id:{anchor - 1}"
        try:
            async for t in client.iter_search_tweets(query=query, max_pages=CHUNK_PAGES):
                tid = str(t.get("id") or "")
                if not tid.isdigit() or int(tid) >= anchor:
                    continue  # boundary leak (max_id is fuzzily inclusive) — already stored
                author_id = str((t.get("author") or {}).get("id") or "")
                if author_id and author_id != str(member_id):
                    continue  # squatter/rename guard, same as forward ingest
                got_any = True
                dt = _parse_dt(t.get("createdAt"))
                batch[tid] = EngTweetRaw(tweet_id=tid, member_id=str(member_id),
                                         created_at=dt, text=(t.get("text") or "")[:4096],
                                         raw=t)
                if int(tid) < min_id:
                    min_id = int(tid)
                if dt is not None and (min_dt is None or dt < min_dt):
                    min_dt = dt
        except Exception as e:  # noqa: BLE001 — one member must never kill the run
            logger.warning("backfill %s (@%s): %s — stopping member, will resume next run",
                           member_id, username, e)
            done_reason = "error"
            inserted += await _flush(batch)
            break
        inserted += await _flush(batch)
        pages_used += CHUNK_PAGES
        reached = min_dt
        if not got_any:
            done_reason = "exhausted"   # start of the account's timeline
            break
        if min_dt is not None and min_dt <= cutoff:
            done_reason = "cutoff"
            break
        if min_id >= anchor:            # no progress despite results -> avoid an infinite loop
            done_reason = "stalled"
            break
        anchor = min_id
    else:
        done_reason = "page_cap"
    credits = client.credits_spent - credits_before
    # "error" members stay not-done so the next run retries them; everything else is final
    await _mark(member_id, tweets=inserted, credits=credits, reached=reached,
                done=done_reason != "error")
    return {"member": username, "inserted": inserted, "reached": reached,
            "reason": done_reason, "usd": credits / 100_000.0}


async def _interim_extract() -> None:
    try:
        from app.engagement.calls import extract_calls
        from app.engagement.extract import extract_edges
        from app.engagement.onchain import enrich_calls
        ex = await extract_edges()
        ca = await extract_calls()
        en = await enrich_calls(limit=300)
        logger.info("interim: +%s edges, +%s calls, %s tokens",
                    ex.get("edges_inserted", 0), ca.get("calls_inserted", 0),
                    en.get("resolved", 0))
    except Exception:  # noqa: BLE001
        logger.exception("interim extract failed (continuing backfill)")


async def main() -> None:
    from app.clients.twitterapi import TwitterAPIClient

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    cutoff = _utcnow() - timedelta(days=WINDOW_DAYS)
    spent_prior = await _spent_prior()
    members = await _universe(cutoff)
    logger.info("backfill up: %s members need history to %s | budget $%.0f (prior spend $%.2f)",
                len(members), cutoff.date(), TOTAL_BUDGET_USD, spent_prior)
    if not members:
        logger.info("BACKFILL COMPLETE — nothing left to do")
        return

    client = TwitterAPIClient(rate_calls=RATE_CALLS, rate_window_s=60)
    sem = asyncio.Semaphore(CONCURRENCY)
    completed = 0
    stop = asyncio.Event()

    def over_budget() -> bool:
        return spent_prior + client.usd_spent >= TOTAL_BUDGET_USD

    async def one(m):
        nonlocal completed
        if stop.is_set():
            return
        async with sem:
            if stop.is_set() or over_budget():
                stop.set()
                return
            r = await backfill_member(client, *m, cutoff=cutoff)
            completed += 1
            if completed % 25 == 0:
                logger.info("progress: %s/%s members | run spend $%.2f (total $%.2f)",
                            completed, len(members), client.usd_spent,
                            spent_prior + client.usd_spent)
            if completed % EXTRACT_EVERY_MEMBERS == 0:
                await _interim_extract()

    for i in range(0, len(members), 200):
        if stop.is_set() or over_budget():
            break
        await asyncio.gather(*(one(m) for m in members[i:i + 200]))

    await _interim_extract()
    logger.info("RUN DONE: %s members this run | run spend $%.2f | total spend $%.2f%s",
                completed, client.usd_spent, spent_prior + client.usd_spent,
                " | BUDGET REACHED" if over_budget() else "")


if __name__ == "__main__":
    asyncio.run(main())
