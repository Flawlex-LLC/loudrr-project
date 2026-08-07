"""Accuracy tests for the TwitterScore scrape — parse real saved fixtures and assert
exact values, so we KNOW the scrape returns correct data before running it at scale.
Hermetic (no network): fixtures captured 2026-06-19."""
import json
import os

from app.clients.twitterscore import (
    parse_profile_html, parse_followed_rows, fmt_tags, clean_account, _int, _num,
)

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def _load(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return f.read()


# ---- profile parse: exact account-level values ----

def test_parse_profile_exact_values():
    d = parse_profile_html(_load("profile_consensysmesh.html"))
    assert d is not None
    assert d["user_id"] == "1131701845964017665"
    assert d["username"] == "ConsensysMesh"
    assert d["twitterscore"] == 65.0
    assert d["band"] == "Good"
    # the key fields the user demanded: category (account-level) + extras
    assert d["categories"] == "Venture Capitals, Projects"
    assert d["based_in"] == "United States"
    assert d["joined_date"] == "May 2019"
    assert d["renamed_count"] == "0 times"
    assert "incubate" in (d["description"] or "")
    # cleanliness + types
    assert isinstance(d["followers"], int) and d["followers"] > 0
    assert "[" not in d["categories"] and "{" not in d["categories"]


def test_parse_profile_passes_clean():
    d = parse_profile_html(_load("profile_consensysmesh.html"))
    cleaned, issues = clean_account(d)
    assert cleaned is not None and not issues, issues   # real account, nothing to repair


# ---- followedBy parse: tags clean, types correct ----

def test_parse_followedby_rows():
    data = json.loads(_load("followedby_czbinance.json")).get("data", [])
    rows = parse_followed_rows(data)
    assert rows, "no rows parsed"
    for r in rows:
        assert r["user_id"] and r["user_id"].isdigit()      # real snowflake id
        assert isinstance(r["tags"], str)                   # clean string, never raw JSON
        assert "[" not in r["tags"] and "{" not in r["tags"]
        if r["twitterscore"] is not None:
            assert 0.0 <= r["twitterscore"] <= 1000.0
        assert r["smart_followers"] is None or isinstance(r["smart_followers"], int)
    # this fixture was the Ecosystems filter -> at least one Ecosystems tag present
    assert any("Ecosystems" in r["tags"] for r in rows)


def test_followedby_rows_pass_clean():
    data = json.loads(_load("followedby_czbinance.json")).get("data", [])
    for r in parse_followed_rows(data):
        cleaned, _ = clean_account(r)
        assert cleaned is not None  # every real followedBy row is keepable


# ---- helpers ----

def test_fmt_tags():
    assert fmt_tags([{"name": "NFT", "categories_name": "Other"}]) == "NFT (Other)"
    assert fmt_tags([{"name": "a16z", "categories_name": "Tier 1 VC"},
                     {"name": "Ethereum", "categories_name": "Ecosystems"}]) == \
        "a16z (Tier 1 VC); Ethereum (Ecosystems)"
    assert fmt_tags([]) == "" and fmt_tags(None) == ""
    assert fmt_tags([{"name": "Solo"}]) == "Solo"  # missing category


def test_num_int_helpers():
    assert _num("1,000") == 1000.0 and _num(None) is None and _num("x") is None
    assert _int("20,118") == 20118 and _int(None) is None and _int("nope") is None


# ---- cleaner: repair-and-keep (drop ONLY unusable no-id rows) ----

def test_clean_keeps_good_unchanged():
    rec = {"user_id": "44196397", "username": "elonmusk", "twitterscore": 1000.0,
           "tags": "a16z (Tier 1 VC)", "categories": "Influencers",
           "followers": 200, "smart_followers": 19}
    cleaned, issues = clean_account(rec)
    assert cleaned is not None and not issues
    assert cleaned["twitterscore"] == 1000.0 and cleaned["tags"] == "a16z (Tier 1 VC)"


def test_clean_drops_only_unusable_noid():
    # the ONLY drop case: no numeric id (blurred/placeholder row)
    cleaned, issues = clean_account({"user_id": "notdigits", "username": "x"})
    assert cleaned is None
    cleaned, _ = clean_account({"user_id": None})
    assert cleaned is None


def test_clean_repairs_but_keeps_account():
    # every other problem -> KEEP the account, null/clean just the bad field (no data hole)
    cleaned, issues = clean_account({"user_id": "12", "username": "has space",
                                     "twitterscore": 1500, "tags": '[{"name":"NFT"}]',
                                     "followers": -5})
    assert cleaned is not None                      # NOT dropped
    assert cleaned["user_id"] == "12"               # account preserved
    assert cleaned["username"] is None              # odd handle nulled
    assert cleaned["twitterscore"] is None          # out-of-range score nulled
    assert cleaned["tags"] == ""                    # leaked json cleared
    assert cleaned["followers"] is None             # negative nulled
    assert issues                                   # repairs recorded


def test_clean_keeps_brackets_in_description():
    # a bio with a bracket must NOT cause a drop/clean (free text)
    cleaned, issues = clean_account({"user_id": "12", "description": "building [stealth] in web3"})
    assert cleaned is not None and cleaned["description"] == "building [stealth] in web3"
    assert not issues
