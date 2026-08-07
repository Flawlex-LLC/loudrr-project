"""Cross-vendor fill: Sorsa-score the top-5k-TwitterScore accounts that we DON'T yet have a
Sorsa score for. This completes both-vendor coverage for the seed candidates so the
disagreement detector can judge every candidate. We already hold their twitter user_id (from
the TwitterScore scrape), so it's a direct /score by id — 1 billable call each, no resolve.

Budget-bounded (leaves RESERVE), record-before-spend, resumable, graceful on quota — same
discipline as sorsa_maximize. python -m scripts.sorsa_crossfill --pilot | --full
"""
import argparse
import asyncio
import os

from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError

from app.clients.sorsa import (SorsaClient, SorsaQuotaError, SorsaBudgetExhausted,
                               SorsaTransientError)
from app.services import harvest as H
from app.services.calibration import _upsert_harvested
from app.db.session import SessionLocal
from app.db.models import HarvestedScore

RESERVE = 150
TOPN = 5000


async def _safe_upsert(rows, tries=10):
    for i in range(tries):
        try:
            await _upsert_harvested(rows)
            return
        except OperationalError as e:
            if "locked" in str(e).lower() and i < tries - 1:
                await asyncio.sleep(1.0 + 1.5 * i)
                continue
            raise


async def _targets():
    """top-TwitterScore accounts (by ts) that have no Sorsa score yet, highest-ts first."""
    async with SessionLocal() as s:
        ts_rows = (await s.execute(text(
            "SELECT user_id, username FROM twitterscore_accounts "
            "WHERE twitterscore IS NOT NULL ORDER BY twitterscore DESC LIMIT :n"),
            {"n": TOPN})).all()
        have = {u for (u,) in (await s.execute(select(HarvestedScore.user_id))).all()}
    return [(u, un) for (u, un) in ts_rows if u not in have]


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pilot", action="store_true")
    ap.add_argument("--full", action="store_true")
    args = ap.parse_args()
    if not (args.pilot or args.full):
        ap.error("choose --pilot or --full")

    client = SorsaClient()
    usage = await client.key_usage_info()
    remaining = int(usage.get("remaining_requests", 0))
    server_spent = int(usage.get("key_requests", 0))
    client.requests_spent = server_spent
    client.hard_ceiling = (server_spent + 30) if args.pilot else (10000 - RESERVE)
    print(f"sorsa remaining={remaining}, valid_until={usage.get('valid_until')}; "
          f"ceiling={client.hard_ceiling} (start {server_spent}, reserve {RESERVE})")
    if client.requests_spent >= client.hard_ceiling:
        print("no budget; aborting."); return

    targets = await _targets()
    if args.pilot:
        targets = targets[:5]
    print(f"cross-fill targets (top-{TOPN} TS, no Sorsa score yet): {len(targets)}")

    # Dedicated resume marker — NOT the shared 'queried' (which also marks accounts that
    # sorsa_maximize only crawled as a top_followers source but never /score'd; reusing it
    # would wrongly skip ~87 gettable top-account scores).
    qpath = os.path.join(H.STATE_DIR, "crossfill_queried.txt")
    queried = H._load_set(qpath)
    qf = open(qpath, "a", encoding="utf-8")

    def spent():
        return client.requests_spent - server_spent

    rows, got, miss = [], 0, 0
    try:
        for uid, un in targets:
            if uid in queried:
                continue
            queried.add(uid); qf.write(uid + "\n"); qf.flush()
            sc = await client.score(user_id=uid)
            if isinstance(sc, (int, float)):
                rows.append({"user_id": uid, "username": un,
                             "sorsa_score": float(sc), "source": "crossfill_ts"})
                got += 1
            else:
                miss += 1
            if len(rows) >= 100:
                await _safe_upsert(rows); rows = []
            if (got + miss) % 100 == 0:
                print(f"  scored={got} no-score={miss} billable={spent()}")
    except (SorsaQuotaError, SorsaBudgetExhausted) as e:
        print(f"  stop (budget/quota): {e}")
    except SorsaTransientError:
        pass
    finally:
        if rows:
            await _safe_upsert(rows)
        qf.close()
    print(f"DONE cross-fill: scored={got} no-score={miss}; billable this run={spent()}")
    try:
        print("final key usage:", await client.key_usage_info())
    except Exception as e:  # noqa: BLE001
        print("usage check failed:", e)


if __name__ == "__main__":
    asyncio.run(main())
