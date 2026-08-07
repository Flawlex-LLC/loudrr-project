# Smart Engagement Data System — Engineering Plan

Status: SHIPPED v1 (2026-07-02) — approved, built TDD (98 tests green), adversarially
reviewed (8 finder angles → 30 verified candidates → 12 findings fixed), live locally with
real data. Seed backfill + enrichment populate continuously.

## Post-review deferred cleanups (tracked, not v1-blocking)
1. **Alembic migrations** (HIGH for deploy): schema lives in scattered `create_all`; the
   `calls_parsed` column already needed a manual ALTER. Wire `alembic upgrade head` into the
   container entrypoint BEFORE the next schema change ships to prod Postgres.
2. **never-500 decorator**: the degrade contract is hand-copied per endpoint in
   `app/engagement/api.py`; factor into one decorator so future endpoints inherit it.
3. **Batch-driver dedup**: `extract.py`/`calls.py` share a line-identical bronze-extraction
   loop (wallets.py has a third variant) — extract one `drive_bronze_extraction` helper.
4. **ON CONFLICT upserts**: pre-filter+unique is a check-then-act race if worker + manual
   runs overlap; SQLAlchemy has `on_conflict_do_nothing` for BOTH sqlite/postgres.
5. **wallets.py scale**: `_store` loads the whole eng_wallet table; `extract_self_posted`
   rescans all bronze with OFFSET paging (fine as manual CLI at seeds-scale; fix with keyset
   pagination + watermark before top:20k).
6. **kol-calls query polish**: per-contract sample LIMIT via window function + a ~60s
   response cache once traffic exists. EngCall.token_key/confidence/chain hold derivable or
   dual-meaning data (documented; consolidate in the next schema migration).

---
Original proposal below (approved with the top-20k universe amendment).

---

## 1. Goals & non-goals

**Goals**
1. **Today:** real data for the profile-page Smart Engagement heatmap — per day, how many
   distinct smart accounts replied to / retweeted / quoted a given account.
2. **Near future ($0 extra fetch):** the same data powers Kaito-style **mindshare**
   (share-of-voice per token across smart-set posts).
3. **Near future ($0 extra fetch):** **KOL calls** — which KOL called which token (ticker or
   contract) when, joined with free onchain metrics (price/mcap/Δ%) → the Bitget
   "KOL signals" experience.
4. **Hard requirement:** a dedicated system that can never break, slow, or drain the
   follower-scoring analytics crawl.

**Non-goals (explicitly out)**
- Likes (structurally uncapturable: no likers feed exists; a like never appears in the liker's
  timeline). UI copy must say "replies, retweets & quotes" — never "engagements incl. likes".
- Real-time/streaming (cost-identical to polling at $0.00015/tweet; worthless for a per-day
  metric; revisit only when a feature needs sub-hour freshness).
- X Lists as source (algorithmic timeline — undercounts replies; needs write-auth to build).
- Fetching a viewed account's engagers (viral fan-out = unbounded cost; our supply-side
  design makes it unnecessary).

## 2. System overview

**Core idea (supply-side inversion):** poll each smart-set member's OWN timeline. Their
outbound replies/RTs/quotes carry the target's identity inside the payload
(`inReplyToUserId`, `retweeted_tweet.author`, `quoted_tweet.author` — verified live on the
gateway 2026-07-02). So one bounded fetch of OUR members yields the inbound engagement
picture for EVERY account they touch. Cost scales with |smart set| × posting rate — never
with a tweet's virality.

```
gateway /user/last_tweets (include_replies=true)      ← the ONLY paid fetch in this system
      │  daily, per smart-set member, incremental since_id
      ▼
eng_tweet_raw   BRONZE  full raw JSON + text per tweet          [one fetch, many features]
      │
      ├──► eng_edge     SILVER  member→target reply/RT/quote edges, dated
      │        └──► GET /v1/smart-engagement  →  heatmap (COUNT DISTINCT engager per day)
      │
      ├──► (later) mindshare attribution: cashtags in raw entities → share-of-voice
      └──► (later) eng_call: $TICKER + contract-address scan over text/raw
               └──► + free onchain join (DexScreener/GeckoTerminal/CoinGecko-demo)
                     →  GET /v1/kol-calls  →  Bitget-style KOL-signals screens
```

## 3. Data model

All in `app/mindshare`-style dedicated module `app/engagement/models.py`, same `Base`,
`eng_*` prefix. ID columns String(64) (wider than smart_set's 32 on purpose: >32-char ids
hard-error on Postgres but pass on SQLite — width must never be the enforcement).

### eng_tweet_raw (bronze) — BUILT
| col | type | why |
|---|---|---|
| tweet_id | String(32) PK | idempotency anchor |
| member_id | String(64) idx | the smart-set member whose timeline this is |
| created_at | DateTime idx | tweet time (UTC-naive) |
| text | Text | cheap later scans (KOL-call tickers/contracts) without JSON parsing |
| raw | JSON | FULL payload — the "never fetch twice" guarantee (entities, nested tweets) |
| parsed | Boolean idx | extraction watermark (crash-resume) |
| ingested_at | DateTime | bookkeeping |

### eng_edge (silver) — BUILT
| col | type | why |
|---|---|---|
| id | Integer PK | |
| tweet_id | String(32) | the engaging tweet |
| engager_id | String(64) idx | smart-set member |
| target_id | String(64) | who they engaged (any account, in-set or not) |
| target_username | String(255) | lowercased at write — API lookup by userName, zero network |
| kind | String(8) | reply \| retweet \| quote |
| ts / day | DateTime / Date idx | engagement time / heatmap bucket |
| captured_at | DateTime | bookkeeping |
- `unique(tweet_id, target_id)` → replays can never double-count.
- `Index(target_id, day)`, `Index(target_username, day)` → the two lookup paths.

### eng_cursor (state) — BUILT
`member_id PK, since_id, last_run_at, tweets_seen` — incremental pulls, replayable.

### eng_call + eng_token (future phase, schema reserved — NOT built yet)
- `eng_call`: id, tweet_id, member_id (KOL), ts, ticker, contract, chain, confidence
  ("contract" = address posted directly, unambiguous; "ticker" = $SYMBOL resolved via
  DexScreener search by top liquidity), price_at_call, mcap_at_call (snapshotted at extract).
- `eng_token`: contract PK, chain, symbol, name, logo, last_price/mcap/Δ, refreshed_at —
  a cache so serving never hits external APIs.

## 4. Ingestion (BUILT)

- **Universe (LOCKED with founder 2026-07-02): the ~3,894 curated seeds — NOT the 98k.**
  The 98k smart_set is the internal PageRank *voting graph* (follow-weight machinery), not a
  product-facing "smart accounts" list — calling 98k accounts "smart followers" would be
  dishonest and tracking even half (~$900/mo) adds engagement from accounts nobody
  recognizes. Seeds are hand-curated crypto anchors = the defensible "smart" set (smaller
  number, higher signal — the Kaito/TweetScout pattern). Highest PageRank first.
  - **KOLs are EMERGENT, not hand-picked:** the KOL roster for /kol-calls = seeds who
    actually post tickers/contracts (e.g. >=3 call-shaped posts in 30d, derived from bronze).
    Self-maintaining, zero extra polling.
  - **Expansion knob is score-threshold, not percentage:** if observed coverage is thin,
    add "top N by PageRank above X" (+2-5k accounts ~ +$40-90/mo), driven by data.
    `ENGAGEMENT_UNIVERSE=all` exists but is a deliberate, separate spend decision.
  - Footnote for later (scoring-side, not this build): the profile page's displayed
    "Smart followers" count derives from the full voter set — decide separately whether
    that number should become seed/tier-based for consistency with "smart engagement."
- **Cadence:** daily worker tick (`RUN_MODE=engagement`, `ENGAGEMENT_TICK_SECONDS=86400`).
  Per-day metric ⇒ per-day polls ⇒ the ~60-credit per-call floor is paid once/member/day.
- **Incremental:** per-member `since_id` cursor; first pull reaches `max_pages` deep
  (default 3 ≈ 60 tweets ≈ 2–6 weeks for typical accounts), later pulls fetch only new.
- **Isolation from the crawl (the hard requirement):**
  - own client instance with own pacing: 15 req/min (crawl uses 30/min; both fit the gateway
    team's 20–40/min band even when running simultaneously);
  - own budget ceiling `ENGAGEMENT_DAILY_BUDGET_USD=10` — scheduling stops when crossed;
  - own tables, own worker process/container; reads `smart_set`, writes nothing shared.
- **Memory/crash safety:** members processed in waves of 50 with one commit per wave
  (bronze rows + cursor advance land atomically); a crash resumes at the next wave; one bad
  member logs a warning and never kills the run.

## 5. Edge extraction (BUILT)

`edges_from_tweet` (pure function) per tweet:
- retweet → target = `retweeted_tweet.author` (both snake/camel key spellings handled)
- reply → target = `inReplyToUserId` / `inReplyToUsername`
- quote → target = `quoted_tweet.author`; a reply-that-quotes emits BOTH edges
- **drop self-engagement** (`engager == target`) — validated as the majority of reply items
  in the wild (accounts thread themselves); keeping them would inflate self-heatmaps
- **drop foreign-authored content** (payload author ≠ polled member) — never fabricate
- malformed payloads → empty list, never an exception
`extract_edges` drives it over `parsed=False` bronze in batches; pre-filter + unique
constraint ⇒ idempotent; parsed flags + edges commit atomically per batch.

## 6. Serving API (BUILT)

```
GET /v1/smart-engagement?userName=X     (also username= / user_id=)
→ 200 {
  "userName": "x", "userId": "123" | null,
  "counts": { "2026-06-20": 2, ... },        # sparse, non-zero days only, ISO keys
  "total": 3, "firstData": "2026-06-20",
  "updated": "...", "coverage": "tracked" | "none"
}
```
- Cell = `COUNT(DISTINCT engager_id)` per day, 364-day window, one indexed GROUP BY.
- Lookup is LOCAL-only: lowercased `target_username` match OR `target_id` via local
  smart_set resolution (handle-change safety). No gateway call in the request path, ever.
- **Never 500s:** any internal failure → `coverage:"none"` + HTTP 200. The public score
  funnel is untouchable by this feature. (Test-enforced.)

## 7. Frontend wiring (NOT built yet — step 4)

- `web/lib/api.ts`: `getSmartEngagement(handle)`; fetched independently of the score call
  (`Promise.allSettled` semantics — a dead engagement API can never break the score card).
- `Profile.engagementDaily?: Record<string, number>` (date-keyed sparse map — kills the
  fixed-364-array off-by-one/timezone bug class).
- `EngagementHeatmap({handle, series?})`: cell = `series[dateKey] ?? 0` when live;
  falls back to the current deterministic mock only when no API is configured (dev).
  `level()` thresholds retuned 5/10/18 → 1/3/6/12 (real distinct-engager counts are small).
- `ScoreDashboard` passes `profile.engagementDaily`.

## 8. Future features on the SAME data (design now, build later)

### Mindshare (Kaito-style)
Same bronze; the existing `app/mindshare` attribution (cashtag → token, engagement ×
author weight) can be pointed at `eng_tweet_raw` over the full seed set. No new fetch.

### KOL calls (the Bitget screens) — $0 in API costs
- **Off-chain half (the calls):** scan `eng_tweet_raw.text` + `raw.entities.symbols` for
  `$TICKER` and contract addresses (base58 = sol, 0x = eth/base). The seeds ARE the KOLs.
  Zero fetch — the data is already ours.
- **On-chain half (mcap/price/Δ%):** free, keyless APIs, snapshot-and-cache pattern:
  - DexScreener (free, no key): live price/mcap/volume/Δ, ticker→contract resolution
  - GeckoTerminal (free, no key, 30/min): historical OHLCV → price-at-call, performance-since
  - CoinGecko Demo (key already in our config): majors/backup metadata
  - Snapshot once at call-extraction; hourly refresh of only actively-called tokens
    (~top 100 ≈ 2 req/min); serve exclusively from our DB. Free-tier limits are never near.
- **Serving:** `/v1/kol-calls` → token leaderboard (calls, distinct KOLs, mcap, Δ%) +
  per-token KOL list + per-KOL call history with performance.

## 9. Failure analysis (what can break what)

| Failure | Blast radius | Guard |
|---|---|---|
| Engagement worker crashes | none outside itself | idempotent stages, wave commits, resumes next tick |
| Gateway 429s/outage | slower engagement data only | tenacity retries honor Retry-After; cursor doesn't advance on failure |
| Budget exhausted | engagement pauses | own $10/day ceiling; crawl wallet guard untouched |
| Viral member (200 RTs/day) | +200 rows | bounded by max_pages; per-tweet cost only |
| Same tweets re-ingested | none | unique(tweet_id, target_id) + pre-filters |
| Engagement API bug/db down | heatmap shows "not tracked" | handler can never 500; frontend allSettled |
| Both workers run at once | still inside gateway band | 15/min + 30/min ≤ 45/min ≈ band ceiling; tunable |

## 10. Testing (TDD — 23 tests green, existing 36 untouched)

- extractor: reply/RT/quote (+key spellings), self-drop, foreign-author drop, reply+quote
  dual edge, malformed-never-crash, username lowercasing, day parse, persist+parsed flag,
  idempotent replay
- API: distinct-collapse, case-insensitive, target_id resolution after handle change,
  unknown → 200 none, missing param → 200 none, forced internal error → 200 none, window bound
- ingest: raw+cursor persistence, incremental second run, seeds-only universe, budget stop,
  bad-member survival, end-to-end run_once → queryable edge

## 11. Cost model

| Item | Cost |
|---|---|
| Pilot (50 seeds, capped $1) | ~$0.10 one-time |
| First full seed pass (3,894 × 3 pages) | ~$3–8 one-time, ~4–5h background |
| Steady state (seeds, daily) | ~$70–120/mo |
| Mindshare reuse | $0 extra fetch |
| KOL calls (off-chain + onchain join) | $0 extra (free APIs, cached) |
| Full 98k universe (only if you flip it) | ~$1.8k/mo daily — separate decision |

## 12. Rollout phases & gates

1. ✅ Backend core + tests (done, $0)
2. Pilot: 50 seeds, `--budget 1` → inspect real edges, verify RT-wrapper dates   **[gate: approval]**
3. Full seed pass (background, ~$3–8) → DB populated                             **[gate: approval]**
4. Frontend wiring → real heatmap on localhost
5. Adversarial code review of full diff → commit
6. Deploy: Coolify app #4, RUN_MODE=engagement (your call when)
7. (next sessions) KOL-call extractor + /v1/kol-calls + UI; mindshare-over-seeds

## 13. Decisions for the founder

1. **Approve pilot + full seed pass spend (~$3–8 total)?**
2. **Cadence:** daily (recommended, ~$70–120/mo) vs 2×/day vs weekly?
3. **KOL-calls priority:** next session after heatmap ships, or sooner?
4. **Deploy timing:** ship the worker to Coolify right after review, or run locally first for a few days?
