# loudrr-analytics-service

A crypto **X (Twitter) influence-scoring layer** — a Sorsa.io / TweetScout-style
service — built on **twitterapi.io** as the sole data source. Standalone backend the
loudrr landing page connects to as a service.

> Score = *who follows you, and how much weight they carry* — not follower count.
> The moat is the curated **smart set `M`** + keeping its follow-graph fresh, not the
> math. Full blueprint: `../django-app/credit_service/docs/scoring_layer_research.md`.
> Cost model: [docs/cost_model.md](docs/cost_model.md).

## How it works

```
seed M (CoinGecko + X Lists)                    ── app/services/seed.py
   └─ crawl each member's *following* ───────────► reverse index (edges)   app/services/crawl.py
                                                     │
       personalized PageRank over M-internal graph ─┘                      app/services/score.py
                                                     │
   score(X) = Σ weights of M-members who follow X ◄──┘ (zero scraping at query time)
                                                     │
                  FastAPI: /score /followers-stats /top-followers ...      app/api/routes.py
```

The crawl of `following` lists builds, in one pass, both the **reverse index**
("who among M follows X", for *any* X) and the **M-internal graph** (fed to PageRank).
Query time is pure DB/compute — no per-query scraping (research §6–7).

## Layout

| Path | What |
|---|---|
| `app/clients/twitterapi.py` | data source; following-IDs (cheap) + profiles (fallback) crawl, budget tracker |
| `app/clients/sorsa.py` | calibration only — pull ground-truth to map our score onto Sorsa's scale |
| `app/db/models.py` | `smart_set`, `edges` (reverse index), `score_snapshots` |
| `app/services/seed.py` | seed import (CoinGecko) + categorization |
| `app/services/crawl.py` | following-graph crawl, USD-budget-guarded, stalest-first |
| `app/services/score.py` | PageRank batch + query-time score/stats/top-followers |
| `app/api/routes.py` | Sorsa-compatible `/v1/*` endpoints |

## Run (local)

```bash
python -m venv .venv && . .venv/Scripts/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                 # fill TWITTERAPI_IO_KEY, DATABASE_URL, ...

python -m scripts.init_db          # create tables (Postgres)
python -m scripts.probe_apis       # preflight: confirms crawl mode + Sorsa quota  (spends a few ¢)
python -m scripts.run_seed --coingecko 1000          # seed projects   (spends ~$0.20)
python -m scripts.run_crawl --limit 100 --budget 5   # pilot crawl     (spends ≤ $5)
python -m scripts.run_score                          # PageRank        (free)
uvicorn app.main:app --reload                        # serve

curl 'localhost:8000/v1/score?username=cz_binance'
```

## Spend gates

Everything except `probe_apis` / `run_seed` / `run_crawl` is free. The **full-M crawl
is the one real cost** (~$360 @ 50k members on the IDs path; ~$750 on profiles). Fund
the twitterapi.io wallet to ≥ $0.50 (50k credits) to unlock 20 QPS. See
[docs/cost_model.md](docs/cost_model.md).

## Status

Scaffold complete; not yet run against the live API (awaiting funded twitterapi.io
wallet + final `M` size). Calibration awaits a topped-up Sorsa key.
