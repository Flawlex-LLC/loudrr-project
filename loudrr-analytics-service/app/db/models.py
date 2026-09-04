"""Data model — the three tables that make scoring pure computation.

The key insight (research §6): crawl the *following* list of each smart-set member
once, and you get BOTH things you need at query time, with zero per-query scraping:

  1. `edges` is the reverse index. A row (follower_id=m, followee_id=X) means
     "smart-set member m follows X". Indexed on followee_id, so "who among M
     follows X?" is a single index range scan — for ANY X, even one never crawled.
  2. The M-internal graph (which M follow which M) is just the subset of `edges`
     where followee_id is itself in `smart_set` — fed to PageRank.

`score(X)` = weighted sum of the PageRank scores of the M-members who follow X.
M-members' own scores come from PageRank over the M-internal graph (stored here).

User IDs are X/Twitter snowflake IDs — stored as TEXT to preserve precision
(twitterapi.io returns them string-encoded for the same reason).
"""
from datetime import datetime
from enum import Enum

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


def _utcnow() -> datetime:
    """Naive UTC — matches the score/edge comparison convention used across the API."""
    from datetime import timezone
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Category(str, Enum):
    """Follower categories. Sorsa exposes 3 (influencer/project/VC); TwitterScore
    uses 8 — we model the richer set and collapse to 3 for the Sorsa-compatible
    /followers-stats response."""
    PROJECT = "project"
    VC = "vc"
    INFLUENCER = "influencer"
    FOUNDER = "founder"
    ANGEL = "angel"
    MEDIA = "media"
    EXCHANGE = "exchange"
    AUDITOR = "auditor"
    UNKNOWN = "unknown"


class SmartSetMember(Base):
    """A curated/labeled crypto account — the moat. Seeded (CoinGecko / X Lists)
    then auto-expanded by follow-graph + PageRank (research §9)."""
    __tablename__ = "smart_set"

    user_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    username: Mapped[str | None] = mapped_column(String(64), index=True)
    display_name: Mapped[str | None] = mapped_column(String(128))
    category: Mapped[str] = mapped_column(String(16), default=Category.UNKNOWN.value, index=True)

    # PageRank score over the M-internal graph; the weight this member contributes
    # to anyone it follows. Written by the score job.
    score: Mapped[float] = mapped_column(Float, default=0.0, index=True)

    # Dense 1..N rank by PageRank score (1 = most central). Persisted so the smart set can be
    # cut to a top-N tier at query time (settings.smart_set_cutoff) without recomputing scores.
    pr_rank: Mapped[int | None] = mapped_column(Integer, index=True)

    # Provenance + freshness
    is_seed: Mapped[bool] = mapped_column(Boolean, default=False)
    seed_source: Mapped[str | None] = mapped_column(String(64))
    following_count: Mapped[int | None] = mapped_column(BigInteger)
    followers_count: Mapped[int | None] = mapped_column(BigInteger)
    protected: Mapped[bool] = mapped_column(Boolean, default=False)

    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_crawled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Edge(Base):
    """A directed follow edge from a smart-set member to anyone (the reverse index).

    follower_id is always a smart_set member. followee_id is arbitrary (may or may
    not be in smart_set). The (followee_id) index is what makes who-follows-X O(log n).
    """
    __tablename__ = "edges"

    follower_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("smart_set.user_id", ondelete="CASCADE"), primary_key=True
    )
    followee_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        # the hot path: "give me every smart-set member who follows X"
        Index("ix_edges_followee", "followee_id"),
    )


class ScoreSnapshot(Base):
    """Periodic score snapshot per account, so /score-changes can report week/month
    deltas (research §2). Keyed by (user_id, captured_at). Covers arbitrary accounts,
    not just M — we snapshot whatever has been queried/scored."""
    __tablename__ = "score_snapshots"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    score: Mapped[float] = mapped_column(Float)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    __table_args__ = (Index("ix_snapshot_user_time", "user_id", "captured_at"),)


# ---- parity: what we pull from TweetScout/Sorsa (docs/parity_strategy.md) ----

class RankedAccount(Base):
    """The PUBLIC serving table: one row per scored account, imported from the crawl's
    ranked outputs (scripts/import_ranked.py). Powers /v1/leaderboard, /v1/profile and
    /v1/search with REAL data only — rank + raw come from the full prod follow-graph
    computation, score is the locked Loudrr calibration of raw, profile fields come from
    the enrichment crawl, categories from the vendor-corroborated account data."""
    __tablename__ = "ranked_accounts"

    user_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    rank: Mapped[int] = mapped_column(Integer, index=True)
    username: Mapped[str] = mapped_column(String(64), index=True)       # stored lowercased
    display_username: Mapped[str | None] = mapped_column(String(64))    # original casing
    name: Mapped[str | None] = mapped_column(String(128))
    bio: Mapped[str | None] = mapped_column(String(1024))
    followers: Mapped[int | None] = mapped_column(BigInteger)
    following: Mapped[int | None] = mapped_column(BigInteger)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    elite_followers: Mapped[int | None] = mapped_column(Integer)   # smart followers (full graph)
    raw_score: Mapped[float | None] = mapped_column(Float)         # unified voter-weight sum
    score: Mapped[int] = mapped_column(Integer, index=True)        # locked Loudrr Score (0-6000)
    categories: Mapped[str | None] = mapped_column(String(128))
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ProfileCache(Base):
    """24h profile cache for ANY looked-up X account that isn't in ranked_accounts.

    We can score any Twitter handle (score_for over our own graph, always fresh — no gateway
    needed), so no real account should ever show "not scored". The ONLY thing we need from
    outside is display metadata (followers/following/pic/bio) — fetched once from the gateway
    on first lookup, cached here, and refreshed when older than PROFILE_TTL. Score is recomputed
    live every request; this table never stores it. Mirrors how Sorsa serves the long tail.
    """
    __tablename__ = "profile_cache"

    user_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    username: Mapped[str] = mapped_column(String(64), index=True)        # lowercased
    display_username: Mapped[str | None] = mapped_column(String(64))
    name: Mapped[str | None] = mapped_column(String(128))
    bio: Mapped[str | None] = mapped_column(String(1024))
    followers: Mapped[int | None] = mapped_column(BigInteger)
    following: Mapped[int | None] = mapped_column(BigInteger)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    image: Mapped[str | None] = mapped_column(String(512))
    location: Mapped[str | None] = mapped_column(String(128))
    refreshed_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, index=True)


# ---- Kaito mindshare arena scrape (docs/kaito_reverse_engineering.md) ----

