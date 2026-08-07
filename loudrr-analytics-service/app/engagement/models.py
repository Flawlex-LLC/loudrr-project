"""Dedicated smart-engagement store (eng_*) — isolated from the follower-scoring crawl.

ARCHITECTURE (locked 2026-07-02 after the supply-side research): we poll each smart-set member's
OWN timeline (their outbound activity) and derive "member engaged target on day D" edges. Cost is
bounded by |smart set| x posting rate — never by a viral tweet's inbound fan-out — and the target
identity is already inside the payload (inReplyToUserId / nested authors), so no per-target fetch
ever happens.

Medallion-lite, built one-fetch-many-features:
  * bronze ``eng_tweet_raw``  — every member timeline tweet, FULL raw JSON + text retained.
      Powers the engagement heatmap today; the same rows later feed mindshare and KOL-call
      extraction (cashtags in raw entities, tickers/contract addresses in text) with ZERO re-fetch.
  * silver ``eng_edge``       — outbound engagement edges (member -> target: reply/retweet/quote).
      Heatmap cell = COUNT(DISTINCT engager_id) per (target, day).
  * state  ``eng_cursor``     — per-member since_id so ingest is incremental + replayable.

ID columns are String(64) intentionally (wider than smart_set's 32): a >32-char id would silently
pass on SQLite but hard-error on Postgres — membership is enforced by equality, never width.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class EngTweetRaw(Base):
    """Bronze: one row per tweet surfaced on a smart-set member's timeline."""

    __tablename__ = "eng_tweet_raw"

    tweet_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    # The smart-set member whose timeline surfaced this tweet (the engager for edge purposes).
    member_id: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    # Text kept separately from raw so future scans (KOL-call tickers/contracts) are cheap.
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw: Mapped[dict] = mapped_column(JSON)
    # Extraction watermarks — one per derived product, so each can be replayed independently.
    # Rows are processed at flag=False and flipped True in the same transaction: a crashed
    # extract simply resumes; a full replay resets the flag.
    parsed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)         # -> eng_edge
    calls_parsed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)   # -> eng_call
    ingested_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class EngEdge(Base):
    """Silver: one outbound engagement edge — member (engager) -> target, dated."""

    __tablename__ = "eng_edge"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tweet_id: Mapped[str] = mapped_column(String(32))
    engager_id: Mapped[str] = mapped_column(String(64), index=True)
    target_id: Mapped[str] = mapped_column(String(64))
    # Lowercased at write time — the public API looks up by userName with zero network calls.
    target_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    kind: Mapped[str] = mapped_column(String(8))  # reply | retweet | quote
    ts: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    __table_args__ = (
        # One edge per (engaging tweet, target): re-ingest/replay can never double-count, and
        # COUNT(DISTINCT engager) is order-independent — the two idempotency guarantees.
        UniqueConstraint("tweet_id", "target_id", name="uq_eng_edge"),
        Index("ix_eng_edge_target_day", "target_id", "day"),
        Index("ix_eng_edge_tuser_day", "target_username", "day"),
    )


class EngCursor(Base):
    """State: newest tweet id already ingested per member (incremental pulls)."""

    __tablename__ = "eng_cursor"

    member_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    since_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    tweets_seen: Mapped[int] = mapped_column(Integer, default=0)


class EngBackfill(Base):
    """Ledger for the one-shot HISTORICAL backfill (scripts/backfill_engagement.py).

    One row per member, upserted after each member completes. Doubles as (a) resume state —
    done members are skipped on restart — and (b) crash-safe spend accounting: the run's
    budget guard subtracts SUM(credits) already in the ledger, so a crash-looping container
    can never re-spend its budget from zero (in-process counters reset on restart; this row
    does not). Forward ingest never touches this table.
    """

    __tablename__ = "eng_backfill"

    member_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tweets: Mapped[int] = mapped_column(Integer, default=0)
    credits: Mapped[float] = mapped_column(Float, default=0.0)
    # oldest created_at after this member's backfill — proof of how far back we reached
    reached: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    done: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class EngCall(Base):
    """Silver: one KOL call — a member-authored post referencing a token.

    ``token_key`` is the raw reference as posted (contract address, or "$TICKER"); resolution
    fills ``token_contract`` (-> eng_token) so ONLY resolved tokens ever reach the UI — a
    garbage base58-looking string simply never resolves and never shows.
    """

    __tablename__ = "eng_call"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tweet_id: Mapped[str] = mapped_column(String(32))
    member_id: Mapped[str] = mapped_column(String(64), index=True)  # the KOL (author)
    ticker: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Token identity columns are 128 not 64: Sui/Aptos-style contracts (0x…::module::TYPE)
    # and DexScreener-resolved addresses legitimately exceed 64 chars — a 66-char value
    # rolled back entire enrichment batches on Postgres (silently passes on SQLite).
    contract: Mapped[str | None] = mapped_column(String(128), nullable=True)  # as-posted
    chain: Mapped[str | None] = mapped_column(String(16), nullable=True)     # sol | evm | resolved chainId
    confidence: Mapped[str] = mapped_column(String(8))  # contract (unambiguous) | ticker (resolved)
    token_key: Mapped[str] = mapped_column(String(128))  # contract or "$TICKER" — dedup anchor
    # Filled by enrichment (app/engagement/onchain.py):
    token_contract: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    price_at_call: Mapped[float | None] = mapped_column(Float, nullable=True)
    mcap_at_call: Mapped[float | None] = mapped_column(Float, nullable=True)
    ts: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    __table_args__ = (
        UniqueConstraint("tweet_id", "token_key", name="uq_eng_call"),
        Index("ix_eng_call_token_day", "token_contract", "day"),
    )


class EngWallet(Base):
    """KOL wallet vault — the identity mapping that powers KOL Rides.

    Confidence tiers (locked): "self" (address posted by the KOL in their own tweet — highest
    trust) > "curated" (Arkham/Dune/Vybe labeled datasets) > "leaderboard" (kolscan/GMGN-derived
    community dumps). Unmatched handles keep member_id NULL so later-added members auto-link.
    Rides must NEVER claim exhaustiveness — always "N tracked KOL wallets".
    """

    __tablename__ = "eng_wallet"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    member_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    handle: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)  # lowercased
    address: Mapped[str] = mapped_column(String(64))
    chain: Mapped[str] = mapped_column(String(8))    # sol | evm
    source: Mapped[str] = mapped_column(String(32))  # tweet | kolquest | bullx | arkham | dune | vybe
    confidence: Mapped[str] = mapped_column(String(12))  # self | curated | leaderboard
    # Loudrr score for the wallet's HANDLE — computed like any web account (score_for: sum of
    # smart-set members who follow them, + 550 base floor if none). NOT from crawling their
    # account. Lets a KOL wallet outside our ranked set still show its real (often low) score,
    # exactly how Sorsa scores the wider web. Populated by scripts/score_wallets_prod.py.
    x_user_id: Mapped[str | None] = mapped_column(String(32), nullable=True)     # resolved X id
    score: Mapped[float | None] = mapped_column(Float, nullable=True)            # 0-6000 Loudrr
    smart_followers: Mapped[int | None] = mapped_column(Integer, nullable=True)
    added_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    __table_args__ = (
        UniqueConstraint("address", "handle", name="uq_eng_wallet"),
        Index("ix_eng_wallet_address", "address"),
    )


class EngRide(Base):
    """KOL Ride: a TRACKED, identity-mapped wallet trading a token (the onchain signal).

    Captured by intersecting keyless pool-trade feeds with the eng_wallet vault; unique
    tx_hash makes every capture idempotent, so history ACCUMULATES beyond the feed's
    short window. Rides are always presented as "tracked KOL wallets" — never exhaustive.
    """

    __tablename__ = "eng_ride"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    token_contract: Mapped[str] = mapped_column(String(128), index=True)  # matches eng_token
    wallet_address: Mapped[str] = mapped_column(String(64))
    member_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    handle: Mapped[str | None] = mapped_column(String(64), nullable=True)  # lowercased
    side: Mapped[str] = mapped_column(String(4))  # buy | sell
    price_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    ts: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    tx_hash: Mapped[str] = mapped_column(String(128))
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    __table_args__ = (
        UniqueConstraint("tx_hash", name="uq_eng_ride_tx"),
        Index("ix_eng_ride_token_ts", "token_contract", "ts"),
    )


class EngToken(Base):
    """Gold-ish cache of token metadata + market data (DexScreener/GeckoTerminal, free).

    Serving reads THIS table only — user traffic never touches external APIs. Refreshed by
    the worker for tokens with recent calls; stale rows just show stale numbers, never errors.
    """

    __tablename__ = "eng_token"

    # 128-wide identity/pool columns: real values exceed 64 (32-byte pool hashes are 66 chars
    # with 0x; Sui/Aptos contracts longer still) — a 66-char pool_address rolled back entire
    # enrichment batches on Postgres. _token_from_pair clamps everything to these widths.
    contract: Mapped[str] = mapped_column(String(128), primary_key=True)
    chain: Mapped[str | None] = mapped_column(String(16), nullable=True)
    symbol: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    image: Mapped[str | None] = mapped_column(String(255), nullable=True)
    price_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    mcap_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    change_24h: Mapped[float | None] = mapped_column(Float, nullable=True)
    liquidity_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    # top pair's pool + GeckoTerminal network id — the keys for free OHLCV (the call chart)
    pool_address: Mapped[str | None] = mapped_column(String(128), nullable=True)
    network: Mapped[str | None] = mapped_column(String(16), nullable=True)
    refreshed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
