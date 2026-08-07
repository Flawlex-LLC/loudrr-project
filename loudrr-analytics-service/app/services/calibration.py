"""Parity harness — harvest TweetScout/Sorsa's own output and fit ours to it.

See docs/parity_strategy.md. Three jobs:
  * ``harvest``       — pull their /score, /score-changes, /followers-stats,
                        /top-followers for a list of accounts; store GroundTruth +
                        upsert HarvestedScore (incl. every top-follower entry, which
                        reconstructs their curated set M with their own scores).
  * ``fit``           — IsotonicRegression mapping our raw score -> their score, on
                        accounts present in both datasets. Monotonic => rank-preserving.
                        Persists knots to data/calibration.json (no sklearn at serve).
  * ``parity_report`` — Spearman/Kendall/MAE on a holdout; how close we are.

Heavy deps (sklearn/scipy/numpy) are imported lazily so the API/crawl don't need them.
"""
from __future__ import annotations

import json
import logging
import os

from sqlalchemy import func, select

from app.clients.sorsa import SorsaClient, SorsaQuotaError
from app.db.models import GroundTruth, HarvestedScore, SmartSetMember
from app.db.session import SessionLocal, engine
from app.services import score as scoring

logger = logging.getLogger(__name__)

CALIB_PATH = os.path.join("data", "calibration.json")


def _dialect_insert(table):
    """Return (insert_construct, row_wise_max_fn) for the configured backend. SQLite's
    2-arg max() is the row-wise max; Postgres uses greatest(). Both inserts expose the
    same on_conflict_do_update/_do_nothing API + .excluded, so the rest is portable."""
    if engine.dialect.name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
        return insert(table), func.max
    from sqlalchemy.dialects.postgresql import insert
    return insert(table), func.greatest


def _dedup_rows(rows: list[dict]) -> list[dict]:
    """Collapse rows by user_id keeping the max sorsa_score. REQUIRED: Postgres
    ON CONFLICT DO UPDATE raises CardinalityViolation if one INSERT batch contains the
    same user_id twice (a single /top-followers payload can list a dup), which would
    abort the irreversible run mid-snowball (review finding #1)."""
    best: dict[str, dict] = {}
    for r in rows:
        uid = r["user_id"]
        if uid not in best or r.get("sorsa_score", 0) > best[uid].get("sorsa_score", 0):
            best[uid] = r
    return list(best.values())


async def _upsert_harvested(rows: list[dict], session=None) -> None:
    """Upsert (account -> their score); keep the max score seen, bump seen_count.

    If ``session`` is given, executes within it and does NOT commit — the caller batches
    many payloads and commits once per flush (the key fix for the per-call WAL-fsync storm
    that destabilized Postgres). If ``session`` is None, opens+commits its own (used by the
    non-batched calibration paths)."""
    rows = _dedup_rows(rows)
    if not rows:
        return
    ins, maxfn = _dialect_insert(HarvestedScore)

    async def _do(s) -> None:
        # chunk so rows*cols stays under SQLite's ~999 bind-variable cap (one big
        # multi-row VALUES upsert otherwise raises "too many SQL variables")
        for i in range(0, len(rows), 100):
            stmt = ins.values(rows[i : i + 100])
            stmt = stmt.on_conflict_do_update(
                index_elements=[HarvestedScore.user_id],
                set_={
                    "username": stmt.excluded.username,
                    "sorsa_score": maxfn(HarvestedScore.sorsa_score, stmt.excluded.sorsa_score),
                    "seen_count": HarvestedScore.seen_count + 1,
                    "last_seen_at": func.now(),
                },
            )
            await s.execute(stmt)

    if session is not None:
        await _do(session)
        return
    async with SessionLocal() as s:
        await _do(s)
        await s.commit()


async def harvest(usernames: list[str], *, with_following: bool = False) -> dict:
    """Pull their data for each account. Returns a run summary; stops cleanly on quota."""
    sorsa = SorsaClient()
    done, quota_hit = 0, False

    for handle in usernames:
        handle = handle.lstrip("@")
        try:
            score = await sorsa.score(handle)
            stats = await sorsa.followers_stats(handle)
            changes = await sorsa.score_changes(handle)
            top_followers = await sorsa.top_followers(handle)
            top_following = await sorsa.top_following(handle) if with_following else []
            # dashboard thousands-score (the true parity target); best-effort, may be None
            dash = await sorsa.dashboard_score(handle)
        except SorsaQuotaError as e:
            logger.warning("Sorsa quota hit after %d accounts: %s", done, e)
            quota_hit = True
            break
        except Exception as e:  # noqa: BLE001 — one bad account shouldn't kill the run
            logger.warning("harvest %s failed: %s", handle, e)
            continue

        async with SessionLocal() as session:
            session.add(
                GroundTruth(
                    username=handle,
                    sorsa_score=score,
                    dashboard_score=(dash or {}).get("score"),
                    dashboard_tier=(dash or {}).get("tier"),
                    week_delta=changes.get("week_delta"),
                    month_delta=changes.get("month_delta"),
                    followers_count=stats.get("followers_count"),
                    influencers_count=stats.get("influencers_count"),
                    projects_count=stats.get("projects_count"),
                    venture_capitals_count=stats.get("venture_capitals_count"),
                    top_followers={"users": top_followers},
                    top_following={"users": top_following} if with_following else None,
                )
            )
            await session.commit()

        # The account itself + every scored top-follower => labeled set / reconstructed M.
        harvested = [{"user_id": handle, "username": handle, "sorsa_score": float(score or 0),
                      "source": "direct"}]
        for tf in top_followers + top_following:
            uid = str(tf.get("id") or tf.get("username") or "").strip()
            if uid and tf.get("score") is not None:
                harvested.append({"user_id": uid, "username": tf.get("username"),
                                  "sorsa_score": float(tf["score"]), "source": "top_follower"})
        await _upsert_harvested(harvested)
        done += 1

    summary = {"harvested_accounts": done, "quota_hit": quota_hit}
    logger.info("harvest done: %s", summary)
    return summary


async def bootstrap_smart_set_from_harvest(*, limit: int = 50_000, min_score: float = 0.0) -> int:
    """Promote harvested accounts (their reconstructed M) into our smart_set so the
    crawler fetches their following lists. This makes our M mirror theirs — the biggest
    parity lever (docs/parity_strategy.md §2 step 2). Only numeric-id accounts; never
    clobbers an existing member's score."""
    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(HarvestedScore.user_id, HarvestedScore.username, HarvestedScore.category)
                .where(HarvestedScore.sorsa_score >= min_score)
                .order_by(HarvestedScore.sorsa_score.desc())
                .limit(limit)
            )
        ).all()
        members = [
            {"user_id": uid, "username": uname, "category": cat or "unknown",
             "is_seed": False, "seed_source": "harvested"}
            for uid, uname, cat in rows if uid.isdigit()
        ]
        if not members:
            return 0
        ins, _ = _dialect_insert(SmartSetMember)
        for i in range(0, len(members), 100):  # chunk under SQLite's bind-variable cap
            stmt = ins.values(members[i : i + 100]).on_conflict_do_nothing(
                index_elements=[SmartSetMember.user_id]
            )
            await session.execute(stmt)
        await session.commit()
    logger.info("bootstrapped %d members into smart_set from harvest", len(members))
    return len(members)


HOLDOUT_MOD = 20  # ~5% of ids held out of the FIT for leakage-free parity measurement


def is_holdout(uid: str, mod: int = HOLDOUT_MOD) -> bool:
    """Deterministic id-hash holdout (no RNG, stable across runs)."""
    return uid.isdigit() and int(uid) % mod == 0


async def _pairs(*, exclude_holdout: bool = False,
                 only_holdout: bool = False) -> tuple[list[float], list[float], list[str]]:
    """(our_raw, their_score, user_id) for accounts present in both datasets.

    Uses harvested entries with a NUMERIC user_id — the top-followers set carries real X
    IDs that overlap our numeric edge index. ``exclude_holdout`` drops the ~5% holdout
    (use for FIT); ``only_holdout`` keeps just it (use for leakage-free parity_report).
    """
    async with SessionLocal() as session:
        rows = (
            await session.execute(select(HarvestedScore.user_id, HarvestedScore.sorsa_score))
        ).all()
    ours, theirs, ids = [], [], []
    for uid, their in rows:
        if not uid.isdigit():
            continue
        ho = is_holdout(uid)
        if (exclude_holdout and ho) or (only_holdout and not ho):
            continue
        raw = (await scoring.score_for(uid)).get("raw", 0.0)
        if raw > 0:  # only accounts our graph actually covers
            ours.append(raw); theirs.append(their); ids.append(uid)
    return ours, theirs, ids


async def fit() -> dict:
    """Fit monotonic our_raw -> their_score on the non-holdout set; persist the knots."""
    import numpy as np
    from sklearn.isotonic import IsotonicRegression

    ours, theirs, _ = await _pairs(exclude_holdout=True)
    if len(ours) < 10:
        return {"fitted": False, "reason": f"only {len(ours)} overlapping accounts (need >=10)"}

    iso = IsotonicRegression(out_of_bounds="clip")
    iso.fit(np.asarray(ours), np.asarray(theirs))

    os.makedirs(os.path.dirname(CALIB_PATH), exist_ok=True)
    with open(CALIB_PATH, "w") as f:
        json.dump({"x": list(map(float, iso.X_thresholds_)),
                   "y": list(map(float, iso.y_thresholds_))}, f)

    return {"fitted": True, "n_fit": len(ours), "path": CALIB_PATH,
            "holdout_parity": await parity_report(on_holdout=True)}


def apply_calibration(raw: float) -> float | None:
    """Map a raw score onto their scale via the persisted isotonic knots (np.interp).
    Returns None if no calibration has been fit yet."""
    if not os.path.exists(CALIB_PATH):
        return None
    import numpy as np
    with open(CALIB_PATH) as f:
        k = json.load(f)
    return float(np.interp(raw, k["x"], k["y"]))


async def parity_report(*, on_holdout: bool = False) -> dict:
    """How close are we? Spearman/Kendall on rank, MAE on the calibrated score.
    ``on_holdout`` measures on the held-out ~5% (leakage-free, the honest number)."""
    import numpy as np
    from scipy.stats import kendalltau, spearmanr

    ours, theirs, _ = await _pairs(only_holdout=on_holdout)
    if len(ours) < 3:
        return {"n": len(ours), "note": "not enough overlap to measure"}
    rho = spearmanr(ours, theirs).correlation
    tau = kendalltau(ours, theirs).correlation
    calibrated = [apply_calibration(o) for o in ours]
    mae = (float(np.mean(np.abs(np.asarray(calibrated) - np.asarray(theirs))))
           if all(c is not None for c in calibrated) else None)
    return {"n": len(ours), "on_holdout": on_holdout, "spearman": round(rho, 4),
            "kendall": round(tau, 4), "mae_calibrated": mae}


async def learn_tiers(handles: list[str], *, client=None) -> dict:
    """Learn the Tier 1-5 score cut-offs from FREE dashboard reads (no API quota).

    Live finding (2026-06-15): API /score == dashboard score == top-follower score (one
    thousands-scale), so we DON'T need a scale regression. We only need the score->tier
    band edges. Pull (score, tier) pairs from app.sorsa.io/profile (free), then
    ``tiers.learn_thresholds`` gives the empirical cut-offs to replace the provisional
    ones in app/core/tiers.py. Returns the learned thresholds + the pairs used."""
    from app.clients.sorsa import SorsaClient
    from app.core.tiers import learn_thresholds

    client = client or SorsaClient()
    pairs: list[tuple[float, int]] = []
    for h in handles:
        dash = await client.dashboard_score(h)   # FREE web read, no API quota
        if dash and dash.get("score") and dash.get("tier"):
            pairs.append((float(dash["score"]), int(dash["tier"])))
    if len(pairs) < 5:
        return {"learned": False, "reason": f"only {len(pairs)} usable dashboard pairs"}
    thresholds = learn_thresholds(pairs)
    path = os.path.join("data", "tier_thresholds.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump({"thresholds": thresholds, "n_pairs": len(pairs)}, f)
    return {"learned": True, "thresholds": thresholds, "n_pairs": len(pairs), "path": path}
