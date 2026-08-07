"""The Loudrr Score — calibration from raw unified weighted-in-degree to the 0–6000 public scale.

raw(X) = Σ PageRank weights of the smart-set voters who follow X  (app.services.score.score_for).

PRIMARY path — the QUANTILE knot map (data/loudrr_calibration_knots.json), written by
scripts/rescore_pruned.py on every re-score. It maps ln(raw) -> Sorsa-scale score,
percentile-for-percentile, so an ON-DEMAND lookup lands on the EXACT same curve that built
the ranked_accounts table (elon 5165, blest 966). This is what keeps /score == the leaderboard.
Requires the score_for raw to be computed on the SAME smart-set tier the knots were fit on —
set SMART_SET_CUTOFF to match the rescore's TOP_N (40000) so the raw domains line up.

FALLBACK path — the older parametric soft-floor / linear / log-cap formula
(data/loudrr_calibration.json). Used only if the knots file is missing, so the API never
hard-fails on a fresh box. Pure-math, zero heavy deps — safe in the API hot path.
"""
from __future__ import annotations

import json
import math
import os

# parametric fallback defaults (locked on the 98,303-voter re-score, 2026-06-28)
_DEFAULTS = {"A": 0.03553, "FLOOR": 0.0, "KLO": 666.0, "KHI": 4000.0, "S": 1000.0, "CAP": 6000.0}
_PARAM_PATH = os.path.join("data", "loudrr_calibration.json")
_KNOTS_PATH = os.path.join("data", "loudrr_calibration_knots.json")
_cache: dict | None = None


def _load() -> dict:
    """Load the quantile knots if present (primary), else the parametric params (fallback).
    Cached; call reset_cache() in tests after swapping the calibration files."""
    global _cache
    if _cache is not None:
        return _cache
    # primary: quantile knots — identical curve to the ranked table
    try:
        with open(_KNOTS_PATH) as f:
            k = json.load(f)
        knots = [(float(a), float(b)) for a, b in k["knots"]]
        if len(knots) >= 2:
            _cache = {"mode": "knots", "knots": knots,
                      "FLOOR": float(k.get("floor", 0.0)), "CAP": float(k.get("cap", 6000.0))}
            return _cache
    except (FileNotFoundError, KeyError, ValueError, TypeError):
        pass
    # fallback: parametric
    p = dict(_DEFAULTS)
    try:
        with open(_PARAM_PATH) as f:
            p.update(json.load(f))
    except FileNotFoundError:
        pass
    p["mode"] = "param"
    _cache = p
    return _cache


def reset_cache() -> None:
    """Drop the cached calibration (test helper / post-redeploy hot-reload)."""
    global _cache
    _cache = None


def params() -> dict:
    """Back-compat accessor (parametric fallback still exposes A/FLOOR/…)."""
    return _load()


def _score_from_knots(raw: float, knots: list[tuple[float, float]], floor: float, cap: float) -> float:
    """Byte-for-byte the rescore's make_scorer: interpolate ln(raw) over the knots, linearly
    extrapolate past either end, clamp to [floor, cap]. Kept identical so an on-demand score
    equals the value scripts/rescore_pruned.py wrote into ranked_accounts."""
    if raw is None or raw <= 0:
        return floor
    x = math.log(raw)
    if x <= knots[0][0]:
        (x0, y0), (x1, y1) = knots[0], knots[1]
        v = y0 + (y1 - y0) / (x1 - x0) * (x - x0)
    elif x >= knots[-1][0]:
        (x0, y0), (x1, y1) = knots[-2], knots[-1]
        v = y1 + (y1 - y0) / (x1 - x0) * (x - x1)
    else:
        v = knots[-1][1]
        for i in range(len(knots) - 1):
            x0, y0 = knots[i]
            x1, y1 = knots[i + 1]
            if x0 <= x <= x1:
                v = y0 + (y1 - y0) * (x - x0) / (x1 - x0)
                break
    return max(floor, min(cap, v))


def loudrr_score(raw: float) -> float:
    """Map a raw unified score to the Loudrr 0–6000 scale (quantile knots, parametric fallback)."""
    c = _load()
    if c["mode"] == "knots":
        return round(_score_from_knots(raw, c["knots"], c["FLOOR"], c["CAP"]), 1)
    # parametric fallback
    A, FLOOR, KLO, KHI, S, CAP = c["A"], c["FLOOR"], c["KLO"], c["KHI"], c["S"], c["CAP"]
    if raw <= 0:
        return FLOOR
    x = A * raw
    if x < KLO:                                   # soft-floor
        return round(FLOOR + (x / KLO) * (KLO - FLOOR), 1)
    if x <= KHI:                                  # linear mid-tier
        return round(x, 1)
    return round(min(CAP, KHI + S * math.log(1 + (x - KHI) / S)), 1)   # log soft-cap
