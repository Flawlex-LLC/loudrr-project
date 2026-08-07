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
    JSON,
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

class GroundTruth(Base):
    """A snapshot of TweetScout/Sorsa's own output for an account — the supervised
    label we fit our score to. One row per (account, harvest run)."""
    __tablename__ = "ground_truth"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    user_id: Mapped[str | None] = mapped_column(String(32), index=True)
    username: Mapped[str | None] = mapped_column(String(64), index=True)

    sorsa_score: Mapped[float | None] = mapped_column(Float)        # their /score API value (small float)
    # dashboard-scale (thousands) score + tier scraped from app.sorsa.io/profile — the
    # number users actually see and the primary parity target (scale differs from API).
    dashboard_score: Mapped[float | None] = mapped_column(Float)
    dashboard_tier: Mapped[int | None] = mapped_column(Integer)
    week_delta: Mapped[float | None] = mapped_column(Float)
    month_delta: Mapped[float | None] = mapped_column(Float)

    # their /followers-stats (categorized counts, NOT real follower count)
    followers_count: Mapped[int | None] = mapped_column(Integer)
    influencers_count: Mapped[int | None] = mapped_column(Integer)
    projects_count: Mapped[int | None] = mapped_column(Integer)
    venture_capitals_count: Mapped[int | None] = mapped_column(Integer)

    top_followers: Mapped[dict | None] = mapped_column(JSON)         # the 20 + their scores
    top_following: Mapped[dict | None] = mapped_column(JSON)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class HarvestedScore(Base):
    """Reconstruction of their curated set M: an account and TweetScout's own score for
    it, collected from /score (direct) and from every /top-followers entry. Becomes both
    our fitting labels AND a prior for our personalization vector (semi-supervised)."""
    __tablename__ = "harvested_scores"

    user_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    username: Mapped[str | None] = mapped_column(String(64), index=True)
    sorsa_score: Mapped[float] = mapped_column(Float, index=True)    # their score for this account
    category: Mapped[str | None] = mapped_column(String(16))         # if inferable
    source: Mapped[str] = mapped_column(String(24))                 # 'direct' | 'top_follower'
    seen_count: Mapped[int] = mapped_column(Integer, default=1)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class TwitterScoreAccount(Base):
    """TwitterScore.io's public score for an account, scraped from their profile page's
    ld+json. SEPARATE company from Sorsa (different 0-1000 scale) — kept in its own table,
    not conflated with harvested_scores. A second independent signal for the smart-set
    list M, its categories, and cross-vendor calibration."""
    __tablename__ = "twitterscore_accounts"

    user_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    username: Mapped[str | None] = mapped_column(String(64), index=True)
    name: Mapped[str | None] = mapped_column(String(128))
    twitterscore: Mapped[float | None] = mapped_column(Float, index=True)  # 0-1000
    band: Mapped[str | None] = mapped_column(String(24))            # "Excellent" etc.
    followers: Mapped[int | None] = mapped_column(BigInteger)
    # TwitterScore's tags (granular affiliations), stored as a clean readable string:
    # "a16z (Tier 1 VC); Ethereum (Ecosystems)". LIKE-filterable, split on "; ".
    tags: Mapped[str | None] = mapped_column(String(512))
    smart_followers: Mapped[int | None] = mapped_column(Integer)
    # account-level fields scraped from the profile page (distinct from `tags`):
    # categories = broad type as a clean string "Venture Capitals, Projects" / "Influencers"
    description: Mapped[str | None] = mapped_column(String(512))
    categories: Mapped[str | None] = mapped_column(String(128))
    based_in: Mapped[str | None] = mapped_column(String(64))
    joined_date: Mapped[str | None] = mapped_column(String(32))
    renamed_count: Mapped[str | None] = mapped_column(String(16))
    # parity with harvested_scores.seen_count: how many accounts list this one as a
    # significant follower (= its in-degree in twitterscore_follows) — a popularity signal.
    seen_count: Mapped[int] = mapped_column(Integer, default=1)
    source: Mapped[str] = mapped_column(String(24))                 # 'profile' | 'followedby' | 'leaderboard'
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


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

class KaitoCapture(Base):
    """Raw fidelity archive: one row per (endpoint, params, run). Stores the FULL JSON
    payload exactly as Kaito's hub.kaito.ai API returned it, so we never lose a field and
    can re-parse retroactively. ``KaitoMindshare`` is the queryable projection of this."""
    __tablename__ = "kaito_captures"

    # BigInteger PK must degrade to INTEGER on SQLite, else it isn't the rowid alias and
    # autoincrement never fires (bulk insert -> NOT NULL id). BIGINT+sequence on Postgres.
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    run_id: Mapped[str] = mapped_column(String(32), index=True)      # one sweep = one run_id
    kind: Mapped[str] = mapped_column(String(16))                    # 'companies' | 'voices'
    vertical: Mapped[str] = mapped_column(String(16))               # crypto | ai | equity | trading
    sector: Mapped[str | None] = mapped_column(String(32))           # ALL | PERP | ... (sub-section)
    duration: Mapped[str | None] = mapped_column(String(8))          # 24h | 7d | 30d | 3m | 12m
    endpoint: Mapped[str] = mapped_column(String(64))               # sector_leaderboard, mindshare_heatmap, ...
    url: Mapped[str] = mapped_column(String(512))
    http_status: Mapped[int | None] = mapped_column(Integer)
    payload: Mapped[dict | list | None] = mapped_column(JSON)        # the raw response body
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    __table_args__ = (Index("ix_kaito_cap_slice", "kind", "vertical", "sector", "duration", "captured_at"),)


class KaitoMindshare(Base):
    """One parsed leaderboard ROW per (slice, entity, run) — the time-series we query.

    ``entity_type`` distinguishes a company/ticker row (companies leaderboards) from a KOL/
    creator row (voices leaderboards). Mindshare is Kaito's normalized share-of-voice (0-1).
    ``raw`` keeps the untouched row dict so no scraped field is ever dropped.
    """
    __tablename__ = "kaito_mindshare"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    run_id: Mapped[str] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(16))                    # companies | voices
    vertical: Mapped[str] = mapped_column(String(16), index=True)
    sector: Mapped[str | None] = mapped_column(String(32), index=True)
    duration: Mapped[str | None] = mapped_column(String(8), index=True)

    rank: Mapped[int | None] = mapped_column(Integer)
    entity_type: Mapped[str] = mapped_column(String(8))             # 'company' | 'kol'
    entity_id: Mapped[str | None] = mapped_column(String(64), index=True)  # company_id or twitter user_id
    handle: Mapped[str | None] = mapped_column(String(64), index=True)     # @username for KOLs
    symbol: Mapped[str | None] = mapped_column(String(32))          # ticker for companies
    name: Mapped[str | None] = mapped_column(String(128))
    logo: Mapped[str | None] = mapped_column(String(256))

    mindshare: Mapped[float | None] = mapped_column(Float)          # share of voice (0-1)
    mindshare_prev: Mapped[float | None] = mapped_column(Float)     # prior-window value (for delta)
    mindshare_delta: Mapped[float | None] = mapped_column(Float)
    followers: Mapped[int | None] = mapped_column(BigInteger)
    smart_followers: Mapped[int | None] = mapped_column(Integer)
    raw: Mapped[dict | None] = mapped_column(JSON)                   # the untouched row
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    __table_args__ = (
        Index("ix_kaito_ms_slice_time", "kind", "vertical", "sector", "duration", "captured_at"),
        Index("ix_kaito_ms_entity_time", "entity_id", "captured_at"),
    )


class TwitterScoreFollow(Base):
    """A TwitterScore 'significant follower' edge: ``follower_id`` follows ``followee_id``,
    surfaced by the followedByList endpoint. These are pre-filtered to influential/scored
    accounts — a free, high-signal head-start on (and cross-check for) the paid follow-graph
    crawl. ``category`` records which filter surfaced it (Tier 1 VC / Ecosystems / ...)."""
    __tablename__ = "twitterscore_follows"

    followee_id: Mapped[str] = mapped_column(String(32), primary_key=True)  # the queried account
    follower_id: Mapped[str] = mapped_column(String(32), primary_key=True)  # a significant follower
    category: Mapped[str | None] = mapped_column(String(24))
    following_date: Mapped[str | None] = mapped_column(String(32))
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("ix_tsfollows_follower", "follower_id"),)
