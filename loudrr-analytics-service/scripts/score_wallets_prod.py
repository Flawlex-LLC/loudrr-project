"""Score every KOL-wallet handle like any web account — Sorsa-style "score the wider web3".

We do NOT crawl these accounts. Their Loudrr score is the SAME model as everyone else's:
score_for(user_id) = Σ scores of smart-set members who follow them, calibrated to 0-6000,
with the 550 base floor when nobody in the smart set follows them. So a KOL wallet outside
our ranked 33.8k still shows a real (often low) score instead of a blank.

Pipeline per distinct handle:
  1. resolve handle -> X user_id (local smart_set first; else one cheap gateway /user/info),
  2. score_for(user_id) over the edges graph -> raw + smart_followers,
  3. loudrr_score(raw) -> 0-6000, written to every eng_wallet row sharing that handle.

Idempotent + resumable: handles already scored are skipped unless FORCE=1.

    DATABASE_URL=<prod> python -m scripts.score_wallets_prod
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys

sys.path.insert(0, ".")

from sqlalchemy import select, update  # noqa: E402

from app.core.loudrr_score import loudrr_score  # noqa: E402
from app.db.session import Base, SessionLocal, engine  # noqa: E402
from app.engagement.models import EngWallet  # noqa: E402
from app.services.resolve import resolve_user_id  # noqa: E402
from app.services.score import score_for  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    stream=sys.stdout)
log = logging.getLogger("score_wallets")

CONCURRENCY = 8
FORCE = os.environ.get("FORCE") == "1"


async def main() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with SessionLocal() as s:
        q = select(EngWallet.handle).where(EngWallet.handle.is_not(None))
        if not FORCE:
            q = q.where(EngWallet.score.is_(None))
        handles = sorted({h for h in (await s.execute(q.distinct())).scalars().all() if h})
    log.info("handles to score: %s (force=%s)", len(handles), FORCE)
    if not handles:
        log.info("nothing to score")
        return

    sem = asyncio.Semaphore(CONCURRENCY)
    done = {"scored": 0, "floor": 0, "unresolved": 0, "gateway": 0}

    async def one(handle: str) -> None:
        async with sem:
            try:
                uid, _ = await resolve_user_id(username=handle)
            except Exception as e:  # noqa: BLE001 — gateway hiccup: leave for next run
                log.warning("resolve @%s: %s", handle, e)
                return
            if not uid:
                done["unresolved"] += 1
                return
            try:
                res = await score_for(uid)
            except Exception as e:  # noqa: BLE001
                log.warning("score @%s: %s", handle, e)
                return
            raw = res.get("raw", 0.0)
            sf = int(res.get("smart_followers", 0))
            score = loudrr_score(raw)
            async with SessionLocal() as s:
                await s.execute(update(EngWallet).where(EngWallet.handle == handle)
                                .values(x_user_id=uid, score=score, smart_followers=sf))
                await s.commit()
            done["scored"] += 1
            if sf == 0:
                done["floor"] += 1

    for i in range(0, len(handles), 200):
        await asyncio.gather(*(one(h) for h in handles[i:i + 200]))
        log.info("progress %s/%s | scored=%s (floor-only=%s) unresolved=%s",
                 min(i + 200, len(handles)), len(handles),
                 done["scored"], done["floor"], done["unresolved"])

    log.info("DONE: scored=%s (floor-only=%s), unresolved=%s of %s handles",
             done["scored"], done["floor"], done["unresolved"], len(handles))


if __name__ == "__main__":
    asyncio.run(main())
