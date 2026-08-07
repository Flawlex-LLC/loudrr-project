"""Import the crawl's ranked outputs into the public serving table (ranked_accounts).

    python -m scripts.import_ranked

Sources (all REAL crawl/vendor outputs, no synthetic data):
  * data/exports/gated_promotion.csv  — DISCOVERED accounts: rank, user_id, username,
    unified_score (raw), elite_followers, following, x_followers (full prod graph)
  * data/exports/elite_voters.csv     — the VOTER universe (vitalik, cz, cobie...):
    loudrr_influence (0-1000) + elite_followers; user_id resolved via profiles_enriched
  * data/exports/profiles_enriched.csv — name, bio, verified, follower counts
  * twitterscore_accounts (DB)         — categories (vendor-corroborated), joined by user_id

Scores: discovered = locked Loudrr calibration of raw; voters = influence x 6 (same
0-6000 display scale; REAL computed influence, replaced by exact score_for at the prod
cutover). The union is re-ranked globally by score. Idempotent: full refresh.
"""
from __future__ import annotations

import asyncio
import csv
import logging
from pathlib import Path

from sqlalchemy import delete, select

from app.core.loudrr_score import loudrr_score
from app.db.models import RankedAccount, TwitterScoreAccount
from app.db.session import Base, SessionLocal, engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("import_ranked")

EXPORTS = Path("data/exports")


def _int(v) -> int | None:
    try:
        return int(float(v)) if v not in (None, "", "NIL") else None
    except (TypeError, ValueError):
        return None


def _float(v) -> float | None:
    try:
        return float(v) if v not in (None, "", "NIL") else None
    except (TypeError, ValueError):
        return None


async def run() -> dict:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    with open(EXPORTS / "profiles_enriched.csv", encoding="utf-8", errors="replace") as f:
        plist = list(csv.DictReader(f))
    profiles = {r["user_id"]: r for r in plist}
    uid_by_uname = {(r.get("username") or "").lower(): r["user_id"] for r in plist if r.get("username")}
    logger.info("profiles_enriched: %s rows", f"{len(profiles):,}")

    rows: list[RankedAccount] = []
    seen_uids: set[str] = set()

    def add(uid: str, uname: str, raw: float | None, score: int, elite, r: dict) -> None:
        if uid in seen_uids:
            return
        seen_uids.add(uid)
        p = profiles.get(uid, {})
        rows.append(RankedAccount(
            user_id=uid,
            rank=0,  # assigned globally after the union sort
            username=uname.lower(),
            display_username=uname,
            name=(p.get("name") or None),
            bio=(p.get("description") or None),
            followers=_int(p.get("followers")) or _int(r.get("x_followers")),
            following=_int(p.get("following")) or _int(r.get("following")),
            verified=str(p.get("blue_verified") or r.get("blue") or "0") in ("1", "True", "true"),
            elite_followers=_int(elite),
            raw_score=raw,
            score=score,
        ))

    with open(EXPORTS / "gated_promotion.csv", encoding="utf-8", errors="replace") as f:
        for r in csv.DictReader(f):
            uid = (r.get("user_id") or "").strip()
            uname = (r.get("username") or "").strip().lstrip("@")
            raw = _float(r.get("unified_score"))
            if not uid or not uname or raw is None:
                continue
            add(uid, uname, raw, int(loudrr_score(raw)), r.get("elite_followers"), r)
    logger.info("discovered rows: %s", f"{len(rows):,}")

    # voters (the core universe: vitalik/cz/cobie...) — influence 0-1000 -> 0-6000 display
    with open(EXPORTS / "elite_voters.csv", encoding="utf-8", errors="replace") as f:
        for r in csv.DictReader(f):
            uname = (r.get("username") or "").strip().lstrip("@")
            infl = _float(r.get("loudrr_influence(0-1000)"))
            uid = uid_by_uname.get(uname.lower())
            if not uname or infl is None or not uid:
                continue
            add(uid, uname, None, int(round(infl * 6)), r.get("elite_followers"), r)
    logger.info("after voters union: %s", f"{len(rows):,}")

    # one coherent global ranking over the union
    rows.sort(key=lambda a: (-a.score, -(a.elite_followers or 0)))
    for i, a in enumerate(rows, start=1):
        a.rank = i

    async with SessionLocal() as s:
        cats = {uid: c for uid, c in (await s.execute(
            select(TwitterScoreAccount.user_id, TwitterScoreAccount.categories)
            .where(TwitterScoreAccount.categories.is_not(None)))).all()}
        for row in rows:
            row.categories = cats.get(row.user_id)

        await s.execute(delete(RankedAccount))  # full refresh — the import IS the source
        s.add_all(rows)
        await s.commit()

    return {"imported": len(rows), "with_categories": sum(1 for r in rows if r.categories)}


if __name__ == "__main__":
    print(asyncio.run(run()))
