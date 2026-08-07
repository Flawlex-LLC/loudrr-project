"""Attribute a tweet to the token entities it's about (which niche/sector mindshare it feeds).

v1 is lexical: cashtags (`$BTC`) + whole-word matches of a token's symbol/id/name against the tweet
text, matched to the token roster. A token can sit in several sectors (e.g. BTC ∈ ALL), so one match
fans out to every sector that token belongs to. LLM/semantic attribution is a later upgrade (P3);
share-of-voice is robust to a little lexical noise since it normalizes per niche.
"""
from __future__ import annotations

import re
from collections import defaultdict

# $TICKER cashtags, and 3-10 char alnum word tokens (uppercased) for symbol/name matching.
_CASHTAG = re.compile(r"\$([A-Za-z][A-Za-z0-9]{0,9})")
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9]{2,}")
# Symbols that are also common English words / CT slang — too generic to match as a BARE word
# (they'd pollute the leaderboard, e.g. "market CAP", "we are NEAR", "built on BASE"). They only
# count via an explicit $cashtag (unambiguous intent) or via their full token NAME. Curated +
# extensible — precision matters more than recall for a public leaderboard.
_AMBIGUOUS = {
    # generic words / acronyms / CT slang
    "ALL", "THE", "FOR", "AND", "ARE", "YOU", "NOT", "NEW", "NOW", "OUT", "GET", "WHO", "WHY",
    "DAO", "NFT", "USD", "CEO", "CFO", "API", "ATH", "FUD", "LFG", "TVL", "DYOR", "GM", "WAGMI",
    # tickers that are ALSO common English words -> require a $cashtag (or full name)
    "ETH", "ART", "GAS", "NEAR", "BASE", "CAP", "LUNA", "MOVE", "CORE", "TON", "BOND", "RARE",
    "MAGIC", "PEOPLE", "TURBO", "SAGA", "WIN", "SUN", "ICE", "OP", "ME", "ID", "ORDER", "POOL",
    "TIME", "HIGH", "LINK", "SAND", "GALA", "ROSE", "FLOW", "ALPHA", "BAND", "KEY", "FORM", "BIO",
}


def build_token_index(token_rows: list) -> dict[str, list[tuple[str, str]]]:
    """key (UPPER symbol / id / single-word name) -> [(sector, entity_id), ...]. Rows are MsRoster
    role='token' objects (need .sector, .entity_id, .symbol, .name)."""
    idx: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for r in token_rows:
        keys = {str(r.entity_id).upper()}
        if r.symbol:
            keys.add(str(r.symbol).upper())
        if r.name and " " not in r.name.strip():
            keys.add(r.name.strip().upper())
        for k in keys:
            idx[k].append((r.sector, str(r.entity_id)))
    return idx


def attribute_tweet(tweet: dict, idx: dict[str, list[tuple[str, str]]]) -> dict[str, set[str]]:
    """Return {entity_id: {sector, ...}} the tweet mentions. ``tweet`` is the raw gateway dict."""
    text = tweet.get("text") or ""
    cashtags = {m.upper() for m in _CASHTAG.findall(text)}
    for s in (tweet.get("entities") or {}).get("symbols") or []:
        t = (s.get("text") or "").upper()
        if t:
            cashtags.add(t)
    words = {w.upper() for w in _WORD.findall(text)}

    out: dict[str, set[str]] = defaultdict(set)
    for cand in cashtags | words:
        if cand in idx and (cand in cashtags or cand not in _AMBIGUOUS):
            for sector, eid in idx[cand]:
                out[eid].add(sector)
    return out
