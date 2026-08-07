"""TDD spec — KOL call extraction from bronze tweets.

A "call" = a member-AUTHORED post (original / quote / reply — NOT a pure retweet) that
references a token by $TICKER cashtag or by contract address (EVM 0x-hex, Solana base58).
Invariants:
  * cashtags come from BOTH text regex and raw entities.symbols,
  * contract mentions outrank cashtags (confidence "contract" vs "ticker"),
  * pure retweets are NOT the member's own call,
  * tweets spraying many tokens (> MAX_TOKENS_PER_TWEET) are spam, not calls,
  * foreign-authored context tweets never produce calls,
  * one call per (tweet, token) — re-extraction never duplicates,
  * fiat cashtags ($USD...) are noise, not tokens.
"""
from datetime import date

import pytest
from sqlalchemy import select

from app.engagement import models as em
from app.engagement.calls import MAX_TOKENS_PER_TWEET, calls_from_tweet, extract_calls

MEMBER = "1001"
CREATED = "Mon Jun 29 06:21:12 +0000 2026"
SOL_CA = "9ybu4ArAY9iyGpjh99eYSmjn5tw3Jvyo9aeRFQqy6Ezh"   # 44-char base58 (PENGU)
EVM_CA = "0x6982508145454ce325ddbe47a25d4ec3d2311933"      # 42-char 0x-hex (PEPE)


def _tweet(tid="c1", text="", author_id=MEMBER, symbols=None, **extra):
    t = {"id": tid, "createdAt": CREATED, "text": text,
         "author": {"id": author_id, "userName": f"user{author_id}"},
         "entities": {"symbols": [{"text": s} for s in (symbols or [])]}, **extra}
    return t


# ── what is a call ──────────────────────────────────────────────────────────

def test_cashtag_in_text_yields_ticker_call():
    calls = calls_from_tweet(_tweet(text="$PENGU is going to melt faces"), MEMBER)
    assert len(calls) == 1
    c = calls[0]
    assert c["ticker"] == "PENGU"
    assert c["contract"] is None
    assert c["confidence"] == "ticker"
    assert c["member_id"] == MEMBER
    assert c["day"] == date(2026, 6, 29)


def test_cashtag_from_entities_symbols():
    calls = calls_from_tweet(_tweet(text="this one.", symbols=["WIF"]), MEMBER)
    assert [c["ticker"] for c in calls] == ["WIF"]


def test_evm_contract_yields_contract_call():
    calls = calls_from_tweet(_tweet(text=f"ape it {EVM_CA}"), MEMBER)
    assert len(calls) == 1
    assert calls[0]["contract"] == EVM_CA.lower()
    assert calls[0]["chain"] == "evm"          # eth vs base resolved at enrichment
    assert calls[0]["confidence"] == "contract"


def test_sol_contract_yields_contract_call():
    calls = calls_from_tweet(_tweet(text=f"CA: {SOL_CA}"), MEMBER)
    assert len(calls) == 1
    assert calls[0]["contract"] == SOL_CA       # base58 stays case-sensitive
    assert calls[0]["chain"] == "sol"


def test_ticker_and_contract_same_tweet_prefers_both_entries():
    calls = calls_from_tweet(_tweet(text=f"$PEPE {EVM_CA}"), MEMBER)
    kinds = {(c["ticker"], c["contract"]) for c in calls}
    assert ("PEPE", None) in kinds
    assert (None, EVM_CA.lower()) in kinds


# ── what is NOT a call ──────────────────────────────────────────────────────

def test_pure_retweet_is_not_a_call():
    t = _tweet(text="RT @x: $PENGU moon",
               retweeted_tweet={"id": "orig", "author": {"id": "9", "userName": "x"}})
    assert calls_from_tweet(t, MEMBER) == []


def test_quote_IS_a_call():
    t = _tweet(text="$PENGU this is the one",
               quoted_tweet={"id": "orig", "author": {"id": "9", "userName": "x"}})
    assert len(calls_from_tweet(t, MEMBER)) == 1


def test_token_spray_is_spam_not_calls():
    spray = " ".join(f"$TOK{i}" for i in range(MAX_TOKENS_PER_TWEET + 1))
    assert calls_from_tweet(_tweet(text=spray), MEMBER) == []


def test_fiat_cashtags_ignored():
    assert calls_from_tweet(_tweet(text="made $USD today, sold for $EUR"), MEMBER) == []


def test_numeric_cashtags_ignored():
    assert calls_from_tweet(_tweet(text="up $100 on the day"), MEMBER) == []


def test_foreign_authored_tweet_yields_nothing():
    assert calls_from_tweet(_tweet(text="$PENGU", author_id="9999"), MEMBER) == []


def test_dedup_within_tweet():
    calls = calls_from_tweet(_tweet(text="$PENGU $pengu $PENGU!!"), MEMBER)
    assert len(calls) == 1                      # case-folded to one token


def test_malformed_never_crashes():
    assert calls_from_tweet({}, MEMBER) == []
    assert calls_from_tweet({"id": "x"}, MEMBER) == []
    assert calls_from_tweet({"id": "x", "text": None}, MEMBER) == []


# ── persistence ─────────────────────────────────────────────────────────────

async def _seed_raw(SessionLocal, tweets):
    async with SessionLocal() as s:
        for t in tweets:
            s.add(em.EngTweetRaw(tweet_id=str(t["id"]), member_id=MEMBER,
                                 created_at=None, raw=t))
        await s.commit()


async def test_extract_calls_persists_and_is_idempotent(eng_db):
    await _seed_raw(eng_db, [
        _tweet("c1", text="$PENGU szn"),
        _tweet("c2", text=f"stealth {SOL_CA}"),
        _tweet("c3", text="no tokens here"),
    ])
    first = await extract_calls()
    assert first["calls_inserted"] == 2

    # full replay (flags reset) must not duplicate
    async with eng_db() as s:
        for r in (await s.execute(select(em.EngTweetRaw))).scalars().all():
            r.calls_parsed = False
        await s.commit()
    second = await extract_calls()
    assert second["calls_inserted"] == 0
    async with eng_db() as s:
        assert len((await s.execute(select(em.EngCall))).scalars().all()) == 2
