"""TweetScout/Sorsa tier model.

CONFIRMED (deep-research 2026-06-15): ascending named Tiers 1-5, derived from the
*dashboard-scale* (thousands) score. Tier 5 "Supreme" is highest. Anchors observed:
  - Tier 5 "Supreme"     ~2905-3835+  (Cointelegraph 2905, opensea ~4222)
  - Tier 4 "Significant"  (exact range unknown)
  - Tier 2 "Noted"        ~289        (predictshark 289)
Tier 1 & Tier 3 names and ALL exact cut-offs are NOT published. So we DON'T hardcode
fake boundaries — we ship reasonable provisional thresholds AND learn the real ones
from harvested (dashboard_score -> tier_name) pairs once the key is live.
"""
from __future__ import annotations

# Names (1->5). Tier 1 & 3 names are guesses pending live confirmation.
TIER_NAMES: dict[int, str] = {
    1: "Emerging",      # provisional — name unconfirmed
    2: "Noted",         # confirmed
    3: "Notable",       # provisional — name unconfirmed
    4: "Significant",   # confirmed
    5: "Supreme",       # confirmed
}

# Provisional dashboard-score lower-bounds per tier. Anchored on the 3 known points
# (Tier 2 ~289, Tier 5 ~2905); the rest are interpolated guesses. These are REPLACED
# by learn_thresholds() after harvesting. Treat as placeholders, not truth.
PROVISIONAL_THRESHOLDS: dict[int, float] = {1: 0, 2: 250, 3: 800, 4: 1600, 5: 2800}


def infer_tier(dashboard_score: float, thresholds: dict[int, float] | None = None) -> dict:
    """Map a dashboard-scale score to {tier, name} using the highest threshold it clears."""
    th = thresholds or PROVISIONAL_THRESHOLDS
    tier = max((t for t, lo in th.items() if dashboard_score >= lo), default=1)
    return {"tier": tier, "name": TIER_NAMES.get(tier, "?")}


def learn_thresholds(pairs: list[tuple[float, int]]) -> dict[int, float]:
    """Learn real per-tier lower-bounds from observed (dashboard_score, tier) pairs.

    Threshold(t) = min observed dashboard_score among accounts labeled tier t. Robust
    to noise via taking the min; gaps are filled by carrying the previous bound.
    """
    by_tier: dict[int, list[float]] = {}
    for score, tier in pairs:
        by_tier.setdefault(int(tier), []).append(float(score))
    learned: dict[int, float] = {}
    prev = 0.0
    for t in sorted(TIER_NAMES):
        if by_tier.get(t):
            prev = min(by_tier[t])
        learned[t] = prev
    return learned
