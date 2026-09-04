# loudrr-analytics-service

**One job: score X accounts.** A score answers *who follows you, and how much
weight do they carry* — not follower count. The moat is the curated **smart set
`M`** and keeping its follow-graph fresh, not the math.

The Loudrr miniapp consumes this service; nothing else does.

> **Scope (2026-08):** this used to also carry Kaito-style mindshare, a smart-
> engagement / KOL-calls tracker with a realtime wallet watcher, vendor-parity
> calibration against Sorsa/TweetScout, and a Next.js dashboard under `web/`.
> All removed — git history has them. What's left is the scoring path only.

## How it works

```
seed M (CoinGecko + X lists)                     ── app/services/seed.py
   └─ crawl each member's *following* ───────────► reverse index (edges)   app/services/crawl.py
                                                     │
       personalized PageRank over M-internal graph ─┘                      app/services/score.py
                                                     │
   raw(X) = Σ weights of M-members who follow X ◄────┘  (zero scraping at query time)
                                                     │
       calibrate raw -> 0..6000 Loudrr Score  ───────┘                     app/core/loudrr_score.py
                                                     │
                  FastAPI /v1/* (read-only)  ────────┘                     app/api/routes.py
```

One crawl pass over `following` lists builds **both** indexes at once: the
reverse index ("who among M follows X", for *any* X) and the M-internal graph
that PageRank runs on. Query time is pure DB — no per-query scraping.

**Calibration is not optional.** `loudrr_score()` maps the raw weight sum onto
the public 0–6000 scale using `data/loudrr_calibration_knots.json` — the same
curve `scripts/rescore_pruned.py` used to write `ranked_accounts`. If that file
is missing the code silently falls back to an older parametric formula, and
on-demand scores stop matching the leaderboard. It is committed (a `!` exception
in `.gitignore`) precisely so it ships inside the image.

## Endpoints

Gated by `X-API-Key` (`ANALYTICS_API_KEY`; unset = keyless, for local dev):
`/v1/score` · `/v1/score-changes` · `/v1/followers-stats` · `/v1/top-followers`
· `/v1/top-following`

Public: `/v1/profile` · `/v1/leaderboard` · `/v1/search` · `/health`

## Layout

| Path | What |
|---|---|
| `app/clients/twitterapi.py` | the ONLY data source — following-IDs (cheap) + profiles, budget tracker |
| `app/db/models.py` | `smart_set`, `edges` (reverse index), `score_snapshots`, `ranked_accounts`, `profile_cache` |
| `app/services/seed.py` | seed import (CoinGecko) + categorization |
| `app/services/crawl.py` | following-graph crawl, USD-budget-guarded, stalest-first |
| `app/services/score.py` | PageRank batch + query-time score / stats / top-followers |
| `app/core/loudrr_score.py` | raw -> 0..6000 calibration (quantile knots, parametric fallback) |
| `app/api/routes.py` | the `/v1/*` endpoints |

## Run (local)

```bash
python -m venv .venv && . .venv/Scripts/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                # fill TWITTERAPI_IO_KEY, DATABASE_URL, ...

python -m scripts.init_db                           # create tables
python -m scripts.run_seed --coingecko 1000         # seed projects   (spends ~$0.20)
python -m scripts.run_crawl --limit 100 --budget 5  # pilot crawl     (spends <= $5)
python -m scripts.run_score                         # PageRank        (free)
uvicorn app.main:app --reload                       # serve

curl 'localhost:8000/v1/profile?userName=cz_binance'
```

## Deploy

One image, two modes (`RUN_MODE` in Coolify):

| RUN_MODE | Does |
|---|---|
| `api` | serves the scoring API on **port 8000** |
| *(unset)* | runs `scripts.run_until_done` — crawls the graph to completion, exits 0 |

`Dockerfile.repair` is a one-shot crawl-repair image (`scripts.repair_crawl2`).
See [docs/deploy_scoring_api.md](docs/deploy_scoring_api.md).

## Scripts

Pipeline: `init_db` · `run_seed` / `seed_smartset` / `seed_coingecko` ·
`run_crawl` / `run_until_done` / `repair_crawl2` / `recrawl_member` ·
`run_score` · `rescore_pruned` (+`rescore_sql`) · `build_ranked_prod` /
`import_ranked` · `resolve_handles_prod` · `profile_enrich`

Ops: `crawl_status` · `audit_crawl_completeness` · `integrity_check` · `backup_db`

## Spend gates

Everything except `run_seed` / `run_crawl` is free. The **full-M crawl is the one
real cost** (~$360 @ 50k members on the IDs path; ~$750 on profiles). See
[docs/cost_model.md](docs/cost_model.md).
