"""KOL call extraction: bronze member tweets -> eng_call rows (token references).

A "call" is a member-AUTHORED post (original / quote / reply) referencing a token by
$TICKER cashtag or contract address. Pure retweets are excluded (amplifying someone
else's call is engagement, not the member's own call — the RT already became an
eng_edge). Zero network: this re-processes tweets we already paid to ingest.

Noise defenses (each spec'd in tests/engagement/test_calls_extract.py):
  * fiat cashtags dropped; numeric "$100" never matches (ticker must start with a letter),
  * a tweet spraying > MAX_TOKENS_PER_TWEET distinct tokens is spam, not calls,
  * EVM spans are masked before the base58 scan (hex can look like base58),
  * garbage base58-lookalikes are harmless: they never RESOLVE (onchain.py), and only
    resolved tokens reach the UI.
"""
from __future__ import annotations

import logging
import re

from sqlalchemy import select

from app.db.session import Base, SessionLocal, engine
from app.engagement.extract import _parse_dt
from app.engagement.models import EngCall, EngTweetRaw

logger = logging.getLogger("eng.calls")

MAX_TOKENS_PER_TWEET = 4  # beyond this it's a shill-list, not a call

_FIAT = {"USD", "EUR", "GBP", "JPY", "CNY", "AUD", "CAD", "CHF", "INR", "KRW"}
_TICKER_RE = re.compile(r"\$([A-Za-z][A-Za-z0-9_]{1,14})\b")
_EVM_RE = re.compile(r"\b(0x[a-fA-F0-9]{40})\b")
_SOL_RE = re.compile(r"\b([1-9A-HJ-NP-Za-km-z]{32,44})\b")


def calls_from_tweet(t: dict, member_id: str) -> list[dict]:
    """All token calls expressed by one member tweet. Pure; never raises."""
    member_id = str(member_id)
    tid = str(t.get("id") or "")
    if not tid:
        return []
    author_id = str((t.get("author") or {}).get("id") or "")
    if author_id and author_id != member_id:
        return []  # foreign context tweet — never attribute
    if isinstance(t.get("retweeted_tweet") or t.get("retweetedTweet"), dict):
        return []  # pure RT: amplification, not the member's own call

    text = t.get("text")
    text = text if isinstance(text, str) else ""

    # tickers: text regex + payload entities
    tickers: set[str] = set()
    for m in _TICKER_RE.finditer(text):
        s = m.group(1).upper()
        if s not in _FIAT:
            tickers.add(s)
    for sym in ((t.get("entities") or {}).get("symbols") or []):
        s = str((sym or {}).get("text") or "").lstrip("$").upper()
        # >= 2 chars, alpha-led — mirrors the text regex ($R etc. is noise, not a token)
        if len(s) >= 2 and s[0].isalpha() and s not in _FIAT:
            tickers.add(s)

    # contracts: EVM first, then mask those spans so hex can't double-match as base58
    contracts: dict[str, str] = {}  # address -> chain hint
    masked = text
    for m in _EVM_RE.finditer(text):
        contracts[m.group(1).lower()] = "evm"
        masked = masked.replace(m.group(1), " " * len(m.group(1)))
    for m in _SOL_RE.finditer(masked):
        cand = m.group(1)
        if not cand.isdigit():
            contracts.setdefault(cand, "sol")

    if len(tickers) + len(contracts) > MAX_TOKENS_PER_TWEET:
        return []  # spray = spam

    ts = _parse_dt(t.get("createdAt"))
    out: list[dict] = []
    for addr, chain in contracts.items():
        out.append({"tweet_id": tid, "member_id": member_id, "ticker": None,
                    "contract": addr, "chain": chain, "confidence": "contract",
                    "token_key": addr, "ts": ts, "day": ts.date()})
    for tick in tickers:
        out.append({"tweet_id": tid, "member_id": member_id, "ticker": tick,
                    "contract": None, "chain": None, "confidence": "ticker",
                    "token_key": f"${tick}", "ts": ts, "day": ts.date()})
    return out


async def extract_calls(batch_size: int = 500) -> dict:
    """Derive calls from all rows not yet calls-parsed. Idempotent; resumes after any crash."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    tweets_parsed = calls_inserted = 0
    while True:
        async with SessionLocal() as s:
            raws = (await s.execute(
                select(EngTweetRaw).where(EngTweetRaw.calls_parsed.is_(False)).limit(batch_size)
            )).scalars().all()
            if not raws:
                break

            candidates: dict[tuple[str, str], dict] = {}
            for r in raws:
                for c in calls_from_tweet(r.raw or {}, r.member_id):
                    candidates.setdefault((c["tweet_id"], c["token_key"]), c)
                r.calls_parsed = True

            existing: set[tuple[str, str]] = set()
            tids = list({k[0] for k in candidates})
            for i in range(0, len(tids), 400):
                rows = (await s.execute(
                    select(EngCall.tweet_id, EngCall.token_key)
                    .where(EngCall.tweet_id.in_(tids[i:i + 400])))).all()
                existing |= {(a, b) for a, b in rows}

            fresh = [EngCall(**c) for k, c in candidates.items() if k not in existing]
            s.add_all(fresh)
            await s.commit()  # flags + calls land atomically per batch

            tweets_parsed += len(raws)
            calls_inserted += len(fresh)

    return {"tweets_parsed": tweets_parsed, "calls_inserted": calls_inserted}
