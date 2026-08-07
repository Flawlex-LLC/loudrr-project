# OSS Stack — license-vetted component → library mapping

> **Date:** 2026-06-15. We ship a **closed-source commercial API**, so every dependency
> is checked for copyleft/source-available traps. Backed by the deep-research run
> (106 agents, 24 sources, 25 claims adversarially verified) + research doc §10.
> **Rule:** prefer MIT/BSD/Apache-2.0. Never link AGPL. For hosted-only SaaS, GPL's
> distribution trigger usually isn't hit — but treat GPL/BSL/SSPL as avoid-by-default
> and get counsel before any exception.

## Adopted (wired in)

| Component | Library | License | Status | Where |
|---|---|---|---|---|
| twitterapi.io client | **thin httpx wrapper** (no SDK exists) | MIT (httpx) | — | `app/clients/twitterapi.py` |
| Async crawl rate-limit | **aiolimiter** 1.2.1 | **MIT** | active (Dec 2024) | `app/clients/twitterapi.py` |
| Scoring engine | **networkx** | **BSD-3** | active | `app/services/score.py` |
| Parity calibration | **scikit-learn** (IsotonicRegression) | **BSD-3** | active | `app/services/calibration.py` |
| Stats (Spearman/Kendall) | **scipy** | **BSD-3** | active | `app/services/calibration.py` |
| HTTP retry | **tenacity** | Apache-2.0 | active | `app/clients/twitterapi.py` |
| Graph store | **Postgres** (asyncpg/SQLAlchemy) | PostgreSQL / MIT / MIT | active | `app/db/` |

**Why no twitterapi.io SDK:** verified — none exists on PyPI/GitHub. The only "official"
artifact (`kaitoInfra/twitterapi-io`, MIT) is an AI-agent *skill* (Markdown docs, `npx
skills add`), not a pip package. Generic X v2 wrappers (tweepy, python-twitter-v2) target
X's official API (different host/auth/params) and **cannot** hit twitterapi.io. Thin
wrapper is the correct, validated choice. Our crawl loop matches their docs verbatim:
`GET /twitter/user/followings`, `X-API-Key` header, `userName` param, cursor pagination
(pageSize 200, stop on `has_next_page=false`/empty `next_cursor`).

## ⚠️ License traps — AVOID (the load-bearing finding)

Every "incremental/streaming PageRank" graph DB we checked is unusable for a closed,
third-party-facing API:

| Option | License | Trap |
|---|---|---|
| **FalkorDB** (RedisGraph successor) | **SSPLv1** | Offering it "as a service or API" triggers **Section 13 → must release your COMPLETE service source**. Hard no (unless commercial license). |
| **Memgraph** Community | **BSL** | Source-available; the only true streaming `pagerank_online` is **Enterprise-gated** from v3.0. No. |
| **ArangoDB** ≥3.12 | **BSL 1.1** | Additional Use Grant explicitly bars commercial SaaS/DBaaS/OEM. No. |
| **Neo4j** Community | **GPLv3** | Copyleft; commercial split. Avoid. |
| **KuzuDB** | (permissive) | Reportedly **abandoned** (Oct 2025) — maintenance risk. Avoid for prod. |
| **graph-tool** | **GPL family** (unverified exact) | Fastest CPU PageRank (~250× networkx) but GPL = copyleft trap; **verify before embedding**. Escape hatch only. |

**Decision:** Postgres adjacency tables + **NetworkX in-process** (BSD). Validated as the
only fully-permissive path. NetworkX is *more* than enough at our scale — PageRank runs
on the **M-internal subgraph** (≤100k nodes, a few M internal edges), not the full
75M-edge reverse index (that stays in Postgres, indexed for who-follows-X lookups).

## Scale-up path (only if perf forces it; not now)

- **cuGraph** — Apache-2.0 (safe!), GPU-only (NVIDIA Volta+/CUDA). Adopt only if we
  provision GPUs. `nx-cugraph` can drop-in behind the NetworkX API.
- **graph-tool** — fastest CPU, but verify its GPL license first (likely a trap).
- **Incremental PPR without a graph DB** — self-implement per **Bahmani et al. (VLDB
  2011)** Monte-Carlo random-walk segments, or **FIRM (SIGMOD 2023)** O(1)/edge index
  maintenance. Peer-reviewed theory, no permissive reference impl → we'd write it.
  Only needed if nightly full-recompute becomes too slow.

## Seed-data sources (Component 7)

- **`zhanymkanov/awesome-web3-twitter-accounts`** (GitHub) — public crypto X account list;
  ingest as additional seed. *(check license before redistributing the list itself.)*
- **CoinGecko** — project handles (`app/services/seed.py`); free, pre-labeled.
- **Your AnkhLabs 1.5K KOL CSV** — already wired (`handles_from_csv`).

## Open / lower-priority (research didn't fully verify; revisit)

- **CoinGecko client**: official `coingecko/coingecko-python` SDK exists alongside
  `man-c/pycoingecko`. Our 2-endpoint httpx use is fine; swap to the official SDK only if
  we need its rate-limit/pro-key handling. Not blocking.
- **Reference repos**: `eleurent/twitter-graph` (fetch→PageRank example),
  `igorbrigadir/awesome-twitter-algo` — read-only inspiration; verify license before
  lifting any code.
- **Bot detection** (later feature): `osome-iu/botometer-python` (API-gated — needs
  RapidAPI + X API), `BunsenFeng/BotRGCN` / `LuoUndergradXJTU/TwiBot-22` (research, check
  licenses). Per research doc §10: build our own XGBoost classifier; harvest *feature
  ideas* only. Don't link research repos with unknown/restrictive licenses.
