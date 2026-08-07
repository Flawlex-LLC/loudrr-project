# Loudrr Mindshare — service architecture

A standalone share-of-voice engine that computes **our own** crypto/AI/trading mindshare + movers
from the loudrr gateway (X data) — modeled on how Kaito does it (see
`docs/kaito_reverse_engineering.md`), but on our follow-graph + PageRank. Lives in `app/mindshare/`,
separate from the PageRank scorer (`app/services/score.py`); owns only the `ms_*` tables.

## Pipeline (medallion)

```
ms_roster ─▶ ingest ─▶ ms_tweet_raw ─▶ score ─▶ ms_contribution ─▶ buckets ─▶ ms_bucket
   (M)        (cursor)   (bronze)      (+attr)    (silver)          (hourly)    (rollup)
                                                                                   │
                                              window = Σ last N hourly buckets ◀───┘
                                                     │  normalize per niche (Σ=1)
                                                     ▼
                                                ms_snapshot ──diff──▶ ms_mover ──▶ /v1/mindshare
                                                  (gold, hourly time-series)         (serve)
```

| Stage | File | Output | Notes |
|---|---|---|---|
| Roster (set M) | `roster.py` | `ms_roster` | from scraped Kaito ∩ `smart_set` (PageRank weight); kol=authors, token=entities |
| Ingest (bronze) | `ingest.py` + client `iter_user_tweets` | `ms_tweet_raw`, `ms_ingest_cursor` | incremental per-author via `since_id`; append-only |
| Attribute | `attribute.py` | (in-memory) | tweet → tokens via cashtags/tickers (LLM later) |
| Score (silver) | `score.py` | `ms_contribution` | `value = engagement × PageRank(author) × retweet_factor` |
| Buckets (rollup) | `aggregate.py:rebuild_buckets` | `ms_bucket` | hourly (sector,entity) sums — **the scale trick** |
| Snapshot (gold) | `aggregate.py:snapshot` | `ms_snapshot` | window = Σ last N buckets, normalized Σ=1/niche, +delta |
| Movers (gold) | `aggregate.py:compute_movers` | `ms_mover` | diff the two latest snapshots → gain/lose/new/exit |
| Orchestrate | `service.py` | — | one hourly tick: ingest→score→buckets→snapshot→movers |
| Serve | `api.py` | HTTP | `/v1/mindshare/{vertical}/{sector}` + `/movers`, read-only |

## Why this scales (the Kaito-shaped decisions)
- **Incremental ingest** — `ms_ingest_cursor.since_id` pulls only tweets newer than last run; an
  hourly tick fetches a handful per KOL. Cost ≈ cents/hour.
- **Hourly buckets** — windowed mindshare = sum of the last N hourly buckets, never a scan of raw
  tweets. Aggregation/serving cost is constant as tweet volume grows.
- **Snapshots = movers for free** — gold snapshots hourly → gainers/losers over *any* interval.
- **Idempotent, decoupled stages** — each stage is a separate runnable communicating via tables;
  re-score (change weights) without re-ingesting; re-aggregate without re-scoring; safe retries.
- **Serving = cheap reads** off pre-aggregated `ms_snapshot`/`ms_mover`.
- **Postgres at scale** — range-partition `ms_tweet_raw`/`ms_bucket`/`ms_contribution` by time and
  drop old partitions (deploy-time migration; plain tables work on both dialects now).

## Tuning / roadmap
- **Weights** (`score.py`): engagement coefficients + `retweet_factor`; author weight = PageRank
  (`smart_set.score` via `ms_roster.weight`), floored to 1.0 until PageRank is recomputed
  (`python -m scripts.run_score`).
- **Attribution** (`attribute.py`): v1 lexical (cashtag/ticker); upgrade to semantic/LLM (P3).
- **Calibrate** against the scraped Kaito numbers (`kaito_mindshare`) — same parity harness as the
  Sorsa work — then re-weight (momentum/freshness) to **beat** them.
- **Engagement freshness**: tweets are scored at first-ingest; re-poll recent tweets' engagement
  later for accuracy.

## Run
```
python -m scripts.scrape_kaito --vertical crypto      # refresh rosters source (hourly/daily)
python -m app.mindshare.roster --vertical crypto      # rebuild set M
python -m app.mindshare.service --vertical crypto     # one hourly tick (cron: RUN_MODE=mindshare)
```
