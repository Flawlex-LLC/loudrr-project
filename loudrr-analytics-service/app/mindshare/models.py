"""Mindshare service tables — the ``ms_*`` namespace (medallion layers).

Kept entirely separate from the scoring model (``app/db/models.py``): the mindshare engine reads
``smart_set.score`` (PageRank weight) and ``kaito_mindshare`` (scraped rosters) but owns nothing
there. Layers:

  roster  -> ms_roster                     # set M per niche (who we track / attribute to)
  bronze  -> ms_tweet_raw, ms_ingest_cursor  # raw tweets + incremental state (append-only)
  silver  -> ms_contribution               # per-(tweet,entity) scored attention
  rollup  -> ms_bucket                     # hourly (entity,sector) weighted buckets  [scale trick]
  gold    -> ms_snapshot, ms_mover         # windowed mindshare + gainers/losers (serve)

BigInteger PKs degrade to INTEGER on SQLite (rowid alias -> autoincrement); BIGINT on Postgres.
The high-volume tables (ms_tweet_raw, ms_bucket) are range-partitioned by time in Postgres — a
deploy-time migration; plain tables here work on both dialects.
"""
from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base

_AUTO_PK = BigInteger().with_variant(Integer, "sqlite")


class MsRoster(Base):
    """Set M per niche: the tracked accounts (role='kol', the *authors* whose tweets count) and the
    tracked entities (role='token', what we *attribute* mentions to). Seeded from the scraped Kaito
    rosters and reconciled to our ``smart_set`` for the PageRank ``weight``."""
    __tablename__ = "ms_roster"

    id: Mapped[int] = mapped_column(_AUTO_PK, primary_key=True, autoincrement=True)
    vertical: Mapped[str] = mapped_column(String(16), index=True)     # crypto | ai | trading
    sector: Mapped[str] = mapped_column(String(32), index=True)       # ALL | TopicPerpDEX | ...
    role: Mapped[str] = mapped_column(String(8), index=True)          # 'kol' | 'token'
    entity_id: Mapped[str] = mapped_column(String(64), index=True)    # twitter user_id | token id
    handle: Mapped[str | None] = mapped_column(String(64), index=True)  # @username (kol)
    symbol: Mapped[str | None] = mapped_column(String(32))            # ticker (token)
    name: Mapped[str | None] = mapped_column(String(128))
    weight: Mapped[float | None] = mapped_column(Float)               # PageRank weight (kol authors)
    in_smart_set: Mapped[bool] = mapped_column(Boolean, default=False)  # did we already crawl/score it
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    source: Mapped[str] = mapped_column(String(24), default="kaito")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("vertical", "sector", "role", "entity_id", name="uq_ms_roster_slot"),
    )


class MsIngestCursor(Base):
    """Incremental ingest state per author — only pull tweets newer than ``since_id``."""
    __tablename__ = "ms_ingest_cursor"

    author_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    since_id: Mapped[str | None] = mapped_column(String(32))          # newest tweet_id seen
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tweets_seen: Mapped[int] = mapped_column(Integer, default=0)


class MsTweetRaw(Base):
    """Bronze: raw tweet + engagement, append-only, idempotent on tweet_id. Kept so we can re-score
    when the weighting model changes without re-ingesting."""
    __tablename__ = "ms_tweet_raw"

    tweet_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    author_id: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    text: Mapped[str | None] = mapped_column(String(4096))
    lang: Mapped[str | None] = mapped_column(String(8))
    like_count: Mapped[int] = mapped_column(Integer, default=0)
    retweet_count: Mapped[int] = mapped_column(Integer, default=0)
    reply_count: Mapped[int] = mapped_column(Integer, default=0)
    quote_count: Mapped[int] = mapped_column(Integer, default=0)
    view_count: Mapped[int] = mapped_column(BigInteger, default=0)
    is_retweet: Mapped[bool] = mapped_column(Boolean, default=False)
    raw: Mapped[dict | None] = mapped_column(JSON)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("ix_ms_tweet_author_time", "author_id", "created_at"),)


class MsContribution(Base):
    """Silver: one scored attention contribution per (tweet, entity). ``value`` = engagement ×
    author weight × recency × quality. Idempotent on (tweet_id, vertical, sector, entity_id)."""
    __tablename__ = "ms_contribution"

    id: Mapped[int] = mapped_column(_AUTO_PK, primary_key=True, autoincrement=True)
    tweet_id: Mapped[str] = mapped_column(String(32), index=True)
    author_id: Mapped[str] = mapped_column(String(64), index=True)
    vertical: Mapped[str] = mapped_column(String(16))
    sector: Mapped[str] = mapped_column(String(32))
    entity_id: Mapped[str] = mapped_column(String(64), index=True)    # token id the tweet is about
    value: Mapped[float] = mapped_column(Float)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)  # tweet created_at
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("tweet_id", "vertical", "sector", "entity_id", name="uq_ms_contrib"),
        Index("ix_ms_contrib_slice_ts", "vertical", "sector", "entity_id", "ts"),
    )


class MsBucket(Base):
    """Hourly rollup — the scale trick. Windowed mindshare = Σ of the last N hourly buckets, so
    aggregation/serving never scans raw tweets. One row per (vertical, sector, entity, hour)."""
    __tablename__ = "ms_bucket"

    id: Mapped[int] = mapped_column(_AUTO_PK, primary_key=True, autoincrement=True)
    vertical: Mapped[str] = mapped_column(String(16))
    sector: Mapped[str] = mapped_column(String(32))
    entity_id: Mapped[str] = mapped_column(String(64))
    hour_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    weighted_value: Mapped[float] = mapped_column(Float, default=0.0)
    mention_count: Mapped[int] = mapped_column(Integer, default=0)

    __table_args__ = (
        UniqueConstraint("vertical", "sector", "entity_id", "hour_ts", name="uq_ms_bucket"),
        Index("ix_ms_bucket_slice_time", "vertical", "sector", "hour_ts"),
    )


class MsSnapshot(Base):
    """Gold: the windowed leaderboard at a point in time (mindshare normalized to Σ=1 per niche).
    Hourly snapshots are the time-series that movers diff."""
    __tablename__ = "ms_snapshot"

    id: Mapped[int] = mapped_column(_AUTO_PK, primary_key=True, autoincrement=True)
    snap_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    vertical: Mapped[str] = mapped_column(String(16))
    sector: Mapped[str] = mapped_column(String(32))
    window: Mapped[str] = mapped_column(String(8))                    # 1h | 24h | 7d | 30d
    entity_id: Mapped[str] = mapped_column(String(64), index=True)
    rank: Mapped[int] = mapped_column(Integer)
    mindshare: Mapped[float] = mapped_column(Float)
    mindshare_delta: Mapped[float | None] = mapped_column(Float)      # vs previous snapshot

    __table_args__ = (
        Index("ix_ms_snap_slice", "vertical", "sector", "window", "snap_ts"),
        UniqueConstraint("snap_ts", "vertical", "sector", "window", "entity_id", name="uq_ms_snap"),
    )


class MsMover(Base):
    """Gold: gainers/losers for a (niche, window) between two snapshots."""
    __tablename__ = "ms_mover"

    id: Mapped[int] = mapped_column(_AUTO_PK, primary_key=True, autoincrement=True)
    snap_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    vertical: Mapped[str] = mapped_column(String(16))
    sector: Mapped[str] = mapped_column(String(32))
    window: Mapped[str] = mapped_column(String(8))
    entity_id: Mapped[str] = mapped_column(String(64), index=True)
    d_mindshare: Mapped[float] = mapped_column(Float)
    d_rank: Mapped[int | None] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(8))                      # gain | lose | new | exit

    __table_args__ = (Index("ix_ms_mover_slice", "vertical", "sector", "window", "snap_ts"),)
