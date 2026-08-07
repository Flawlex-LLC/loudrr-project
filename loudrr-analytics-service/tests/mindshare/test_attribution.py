"""TDD spec — tweet→token attribution must be Kaito-grade: catch real mentions, reject noise.

Hermetic (no DB/network). Encodes the behaviour our mindshare needs to resemble Kaito: precise
cashtag/ticker attribution WITHOUT the false positives that pollute a leaderboard (BASE, CAP, NEAR,
LUNA et al. are common English words and must NOT match as bare words — only via an explicit
$cashtag). These tests are the acceptance bar; the impl is tuned until they pass.
"""
from types import SimpleNamespace

from app.mindshare.attribute import attribute_tweet, build_token_index


def _tok(entity_id, name=None, sector="ALL", symbol=None):
    return SimpleNamespace(entity_id=entity_id, name=name or entity_id,
                           symbol=symbol or entity_id, sector=sector)


# A roster covering distinctive symbols AND common-word symbols (the trap).
ROSTER = [
    _tok("BTC", "BITCOIN"), _tok("SOL", "SOLANA"), _tok("ETH", "ETHEREUM"),
    _tok("HYPERLIQUID", "HYPERLIQUID"), _tok("POLYMARKET", "POLYMARKET"),
    _tok("NEAR", "NEAR"), _tok("BASE", "BASE"), _tok("CAP", "CAP"), _tok("LUNA", "LUNA"),
    _tok("HYPERLIQUID", "HYPERLIQUID", sector="EXCHANGE"),   # token in two sectors
]
IDX = build_token_index(ROSTER)


def _hits(text, entities=None):
    return attribute_tweet({"text": text, "entities": entities or {}}, IDX)


# ---- precise matches we MUST catch -------------------------------------------------

def test_cashtag_matches():
    assert "BTC" in _hits("just aped into $BTC and $SOL")
    assert "SOL" in _hits("just aped into $BTC and $SOL")


def test_distinctive_symbol_bare_word_matches():
    assert "HYPERLIQUID" in _hits("HYPERLIQUID volume is insane")
    assert "POLYMARKET" in _hits("trading the election on POLYMARKET")


def test_name_matches():
    assert "BTC" in _hits("Bitcoin is digital gold")
    assert "SOL" in _hits("Solana fees are tiny")


def test_entities_symbols_field_used():
    assert "ETH" in _hits("staking rewards", entities={"symbols": [{"text": "ETH"}]})


def test_multi_sector_fanout():
    hits = _hits("HYPERLIQUID perp")
    assert hits.get("HYPERLIQUID") == {"ALL", "EXCHANGE"}


# ---- noise we MUST reject (common English words as bare tickers) -------------------

def test_common_word_tickers_not_matched_as_bare_words():
    assert "NEAR" not in _hits("we are near the launch date")
    assert "CAP" not in _hits("the market cap is huge")
    assert "BASE" not in _hits("built on a solid base of users")
    assert "LUNA" not in _hits("under the luna moonlight")


def test_common_word_ticker_still_matches_via_cashtag():
    # an explicit cashtag is unambiguous intent — it SHOULD match even for common words
    assert "NEAR" in _hits("accumulating $NEAR here")
    assert "BASE" in _hits("bridging to $BASE")


def test_bare_eth_is_ambiguous_without_cashtag():
    # 'eth' shows up inside many words/handles; bare mention shouldn't auto-attribute
    assert "ETH" not in _hits("she said tarporeth or something")
