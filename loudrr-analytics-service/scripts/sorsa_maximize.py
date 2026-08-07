"""Maximize the REMAINING Sorsa API quota on QUALITY (per owner's plan).

Two passes, budget-bounded (leaves a reserve), record-before-spend, resumable, graceful
on quota exhaustion — never blows the one-time key:

  PASS 1 — CoinGecko coverage: score every CoinGecko handle we don't yet have a Sorsa
           score for (resolve ids via info_batch, then /score). "scan all coingecko."
  PASS 2 — >300 rescan: crawl /top-followers ONLY of accounts with sorsa_score > 300
           (high-quality core); enqueue newly-found accounts that ALSO score >300. This
           spends the scarce balance discovering more HIGH-quality accounts, not noise.

    python -m scripts.sorsa_maximize --pilot     # ~tiny, verify
    python -m scripts.sorsa_maximize --full       # use the remaining quota (minus reserve)

Writes harvested_scores (via calibration._upsert_harvested). Resumable via queried_ids.
"""
import argparse
import asyncio
import os
import sys

from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app.clients.sorsa import (SorsaClient, SorsaQuotaError, SorsaBudgetExhausted,
                               SorsaTransientError)
from app.services import harvest as H
from app.services.calibration import _upsert_harvested
from app.db.session import SessionLocal
from app.db.models import HarvestedScore

CG_FILE = os.path.join("data", "coingecko_handles.txt")
RESERVE = 150          # leave this many calls unspent (calibration/spot-checks later)
MIN_RECURSE = 300.0    # only rescan/expand through accounts scoring above this


async def _safe_upsert(rows, tries=10):
    """Upsert with retry-on-'database is locked' — the snowball is writing the same SQLite
    file concurrently and aiosqlite doesn't reliably honor busy_timeout, so back off + retry
    until its batch-commit frees the write lock."""
    for i in range(tries):
        try:
            await _upsert_harvested(rows)
            return
        except OperationalError as e:
            if "locked" in str(e).lower() and i < tries - 1:
                await asyncio.sleep(1.0 + 1.5 * i)
                continue
            raise


def _cg_handles():
    if not os.path.exists(CG_FILE):
        return []
    return [ln.strip().lstrip("@") for ln in open(CG_FILE, encoding="utf-8") if ln.strip()]


async def _scored_usernames():
    async with SessionLocal() as s:
        return {u.lower() for (u,) in (await s.execute(
            select(HarvestedScore.username).where(HarvestedScore.username.isnot(None)))).all()}


async def _over300_uncrawled(queried: set[str]):
    async with SessionLocal() as s:
        rows = (await s.execute(
            select(HarvestedScore.user_id).where(HarvestedScore.sorsa_score > MIN_RECURSE))).all()
    return [u for (u,) in rows if u not in queried]


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pilot", action="store_true")
    ap.add_argument("--full", action="store_true")
    args = ap.parse_args()
    if not (args.pilot or args.full):
        ap.error("choose --pilot or --full")

    client = SorsaClient()
    usage = await client.key_usage_info()      # FREE (not billable)
    remaining = int(usage.get("remaining_requests", 0))
    server_spent = int(usage.get("key_requests", 0))
    # Budget on ACTUAL billable calls (top_followers paginates -> many HTTP calls/logical
    # call). Seed requests_spent from the server's authoritative count; the client's
    # hard_ceiling then stops us precisely, leaving RESERVE unspent of the 10k cap.
    client.requests_spent = server_spent
    client.hard_ceiling = (server_spent + 30) if args.pilot else (10000 - RESERVE)
    print(f"sorsa remaining={remaining}, valid_until={usage.get('valid_until')}; "
          f"spend ceiling={client.hard_ceiling} (start {server_spent}, reserve {RESERVE})")
    if client.requests_spent >= client.hard_ceiling:
        print("no budget; aborting."); return

    qpath = H._queried_path(H.STATE_DIR)
    queried = H._load_set(qpath)
    scored = await _scored_usernames()
    qf = open(qpath, "a", encoding="utf-8")

    def spent():
        return client.requests_spent - server_spent   # billable calls THIS run

    def mark(uid):
        if uid not in queried:
            queried.add(uid); qf.write(uid + "\n"); qf.flush()

    try:
        # ---- PASS 1: CoinGecko coverage ----
        missing = [h for h in _cg_handles() if h.lower() not in scored]
        if args.pilot:
            missing = missing[:5]
        print(f"PASS 1 — CoinGecko: {len(missing)} handles need a Sorsa score")
        resolved = []
        try:
            for i in range(0, len(missing), 100):
                got = await client.info_batch(usernames=missing[i:i + 100])
                for p in got:
                    uid = str(p.get("id") or "")
                    if uid.isdigit():
                        resolved.append((uid, p.get("username")))
        except (SorsaQuotaError, SorsaBudgetExhausted) as e:
            print(f"  stop during resolve: {e}")
        except SorsaTransientError:
            pass
        cg_rows = []
        try:
            for uid, un in resolved:
                if uid in queried:
                    continue
                mark(uid)
                sc = await client.score(user_id=uid)
                if isinstance(sc, (int, float)):
                    cg_rows.append({"user_id": uid, "username": un,
                                    "sorsa_score": float(sc), "source": "cg_direct"})
                if len(cg_rows) >= 100:
                    await _safe_upsert(cg_rows); cg_rows = []
        except (SorsaQuotaError, SorsaBudgetExhausted) as e:
            print(f"  stop (quota) during CoinGecko score: {e}")
        except SorsaTransientError:
            pass
        if cg_rows:
            await _safe_upsert(cg_rows)
        print(f"  CoinGecko coverage done; billable calls so far={spent()}")

        # ---- PASS 2: >300-gated top-followers snowball ----
        frontier = await _over300_uncrawled(queried)
        if args.pilot:
            frontier = frontier[:3]
        seen = set(frontier)
        print(f"PASS 2 — >300 rescan: {len(frontier)} un-crawled >300 accounts")
        buf, new_found, crawled = [], 0, 0
        try:
            while frontier:
                uid = frontier.pop()
                if uid in queried:
                    continue
                mark(uid)
                users = await client.top_followers(user_id=uid); crawled += 1
                rows, _edges = H._tf_rows(users)
                buf += rows
                for r in rows:
                    if (r["sorsa_score"] > MIN_RECURSE and r["user_id"] not in seen
                            and r["user_id"] not in queried):
                        seen.add(r["user_id"]); frontier.append(r["user_id"]); new_found += 1
                if len(buf) >= 300:
                    await _safe_upsert(buf); buf = []
                if crawled % 100 == 0:
                    print(f"  crawled={crawled} new>300_queued={new_found} billable={spent()} frontier={len(frontier)}")
        except (SorsaQuotaError, SorsaBudgetExhausted) as e:
            print(f"  stop (budget/quota): {e}")
        except SorsaTransientError:
            pass
        if buf:
            await _safe_upsert(buf)
        print(f"  >300 rescan done; crawled={crawled} new>300 queued={new_found}; billable this run={spent()}")
    finally:
        qf.close()

    try:
        print("final key usage:", await client.key_usage_info())
    except Exception as e:  # noqa: BLE001
        print("usage check failed:", e)


if __name__ == "__main__":
    asyncio.run(main())
