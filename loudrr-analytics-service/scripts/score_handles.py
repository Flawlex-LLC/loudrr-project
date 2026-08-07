"""Direct-/score curated handles the top-followers crawl structurally MISSES
(low-out-degree orgs that follow ~nobody + mid-score accounts under the 20-cap).

    python -m scripts.score_handles --file data/twitterscore_top100.txt

Resolve handle->id+bio (info_batch), pull /score, upsert. Only scores handles NOT
already in the list (cheap, ~1 call/account). SAFE: additive upsert only, no deletes.
MUST NOT run while a harvest is using the key (no concurrent key use).
"""
import argparse
import asyncio

from sqlalchemy import text

from app.clients.sorsa import (
    SorsaBudgetExhausted,
    SorsaClient,
    SorsaQuotaError,
    SorsaTransientError,
)
from app.core.config import settings
from app.db.session import SessionLocal, engine
from app.services.calibration import _upsert_harvested
from app.services.seed import categorize


async def _current_usernames() -> set[str]:
    async with engine.begin() as c:
        rows = (await c.execute(
            text("select lower(username) from harvested_scores where username is not null"))).all()
    return {r[0] for r in rows}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="data/twitterscore_top100.txt")
    args = ap.parse_args()

    handles = [ln.strip() for ln in open(args.file, encoding="utf-8") if ln.strip()]
    have = await _current_usernames()
    missing = [h for h in handles if h.lower() not in have]
    print(f"{len(missing)} of {len(handles)} not yet in the list -> direct-scoring them")
    if not missing:
        print("nothing to do"); return

    client = SorsaClient(hard_ceiling=settings.sorsa_request_ceiling)

    # 1) resolve handle -> id (+ bio for categorization)
    profiles: list[dict] = []
    for i in range(0, len(missing), 100):
        try:
            profiles += await client.info_batch(usernames=missing[i : i + 100])
        except (SorsaQuotaError, SorsaBudgetExhausted, SorsaTransientError) as e:
            print(f"  ! resolve stopped: {e}"); break
    print(f"  resolved {len(profiles)} profiles")

    # 2) /score each, build rows (skip on per-account failure; stop cleanly on budget)
    rows: list[dict] = []
    for p in profiles:
        uid = str(p.get("id") or "")
        if not uid.isdigit():
            continue
        try:
            sc = await client.score(user_id=uid)
        except (SorsaQuotaError, SorsaBudgetExhausted) as e:
            print(f"  ! scoring stopped (budget): {e}"); break
        except SorsaTransientError as e:
            print(f"  ! skip {p.get('username')}: {e}"); continue
        if sc and sc > 0:
            rows.append({
                "user_id": uid, "username": p.get("username"),
                "sorsa_score": float(sc),
                "category": categorize(p.get("display_name"), p.get("description")).value,
                "source": "direct",
            })

    # 3) additive upsert (chunked, dialect-aware)
    if rows:
        async with SessionLocal() as s:
            await _upsert_harvested(rows, session=s)
            await s.commit()
    print(f"added/updated {len(rows)} accounts via direct /score (spent {client.requests_spent} this pass)")
    try:
        print("balance:", await client.key_usage_info())
    except SorsaQuotaError as e:
        print("balance:", e)


if __name__ == "__main__":
    asyncio.run(main())
