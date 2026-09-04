"""Unit tests for the pure logic — no DB, no network, no API key needed.

    pytest -q
"""
from app.clients.twitterapi import (
    _FOLLOWING_PROFILE_CREDITS,
    _per_item_credits,
)
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

