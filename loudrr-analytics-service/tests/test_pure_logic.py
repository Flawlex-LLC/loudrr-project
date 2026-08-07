"""Unit tests for the pure logic — no DB, no network, no API key needed.

    pytest -q
"""
from app.clients.twitterapi import (
    _FOLLOWING_PROFILE_CREDITS,
    _per_item_credits,
)
from app.core.tiers import infer_tier, learn_thresholds
from app.core.util import username_from_link
from app.db.models import Category
from app.services.seed import categorize


def test_link_parsing():
    assert username_from_link("https://x.com/cz_binance") == "cz_binance"
    assert username_from_link("https://twitter.com/@a16z/status/1") == "a16z"
    assert username_from_link("https://x.com/i/lists/123") is None  # not a profile
    assert username_from_link("not a link") is None


def test_credit_tiers_pick_cheapest_at_max_page():
    # following PROFILES: 200/page -> 1 credit tier; small pages -> 3 credits
    assert _per_item_credits(_FOLLOWING_PROFILE_CREDITS, 200) == 1
    assert _per_item_credits(_FOLLOWING_PROFILE_CREDITS, 150) == 2
    assert _per_item_credits(_FOLLOWING_PROFILE_CREDITS, 50) == 3


def test_categorize_heuristic():
    assert categorize("Paradigm", "We invest in crypto. ventures") == Category.VC
    assert categorize("Some Auditor", "smart contract audit firm") == Category.AUDITOR
    assert categorize("Random Person", "i like sunsets") == Category.UNKNOWN


def test_tier_inference_matches_confirmed_anchors():
    # confirmed anchors from algo research: Cointelegraph 2905 -> Tier 5 Supreme,
    # predictshark 289 -> Tier 2 Noted
    assert infer_tier(2905) == {"tier": 5, "name": "Supreme"}
    assert infer_tier(289)["name"] == "Noted"
    assert infer_tier(0)["tier"] == 1


def test_learn_thresholds_uses_min_observed_per_tier():
    learned = learn_thresholds([(2905, 5), (4222, 5), (289, 2), (3835, 5)])
    assert learned[5] == 2905.0  # min score seen at tier 5
    assert learned[2] == 289.0
    # gaps carry forward the previous bound (monotonic, no inversion)
    assert learned[3] >= learned[2] and learned[4] >= learned[3]


# ---- harvest orchestration pure logic ----

from app.services.harvest import (  # noqa: E402
    edge_local_verdict,
    gate_split,
    novelty_summary,
    _tf_rows,
)
from app.services.calibration import is_holdout  # noqa: E402


def test_gate_split_breadth_always_nonzero_and_sums_to_pool():
    # refuted assumption "core is enough" => breadth must never be zero
    for n in (0, 2.9, 3, 7.9, 8, 20):
        snow, breadth = gate_split(n, 8160)
        assert snow + breadth == 8160
        assert breadth > 0
    # more breadth when the core saturates fast (low N)
    assert gate_split(1, 8160)[1] > gate_split(10, 8160)[1]


def test_is_holdout_deterministic_5pct():
    assert is_holdout("20") and is_holdout("40")
    assert not is_holdout("21")
    assert not is_holdout("notnumeric")


def test_novelty_summary_window():
    # short series -> last-third fallback
    s = novelty_summary([20, 18, 4, 2])
    assert s["calls"] == 4 and s["n"] >= 0
    # long series -> uses [200,300)
    long = [18] * 200 + [3] * 100
    assert novelty_summary(long)["n"] == 3.0


def test_edge_local_verdict():
    # invariant across parents -> global (not edge-local)
    glob = {str(i): [100.0, 100.5, 99.8] for i in range(10)}
    assert edge_local_verdict(glob)["edge_local"] is False
    # wildly varying per parent -> edge-local
    loc = {str(i): [10.0, 900.0, 5.0] for i in range(10)}
    assert edge_local_verdict(loc)["edge_local"] is True
    # too few multi-parent obs -> undecided, default global
    assert edge_local_verdict({"1": [5.0, 5.0]})["decided"] is False


def test_dedup_rows_keeps_max_score_per_uid():
    # a single /top-followers payload listing the same uid twice must NOT reach the
    # upsert un-collapsed (would raise Postgres CardinalityViolation -> abort the run)
    from app.services.calibration import _dedup_rows
    rows = [
        {"user_id": "1", "sorsa_score": 100.0, "source": "top_follower"},
        {"user_id": "1", "sorsa_score": 300.0, "source": "top_follower"},
        {"user_id": "2", "sorsa_score": 50.0, "source": "top_follower"},
    ]
    out = {r["user_id"]: r["sorsa_score"] for r in _dedup_rows(rows)}
    assert out == {"1": 300.0, "2": 50.0}


def test_tf_rows_rejects_bad_ids_and_scores():
    users = [
        {"id": "123", "username": "a", "score": 4100.5, "display_name": "A", "description": ""},
        {"id": "notnum", "username": "b", "score": 50},      # non-numeric id -> rejected
        {"id": "456", "username": "c", "score": None},        # null score -> rejected
        {"id": "789", "username": "d", "score": True},        # bool -> rejected
        {"id": "789", "username": "d", "score": 200},         # valid
    ]
    rows, edges = _tf_rows(users)
    ids = {r["user_id"] for r in rows}
    assert ids == {"123", "789"}
    assert all(isinstance(s, float) for _, s in edges)
