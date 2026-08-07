# Deploying Loudrr Mindshare

One Docker image, dispatched by `RUN_MODE` (see `Dockerfile`). Mindshare adds a third mode to the
existing `api` / crawl pattern. Storage is Postgres in prod (the `ms_*` tables auto-create on first
run via `Base.metadata.create_all`; a partitioning migration is a later optimization).

## Processes (Coolify resources)

| RUN_MODE | command | what it is | schedule |
|---|---|---|---|
| `api` | `uvicorn app.main:app` | public read API incl. `/v1/mindshare/*` | long-running |
| `mindshare` | `python -m app.mindshare.worker` | hourly ingest→score→aggregate→movers | long-running |
| (default) | `python -m scripts.run_until_done` | follow-graph crawl | one-shot |

Plus one **scheduled job** (Coolify scheduled task / cron) to keep the roster source fresh:

```
# daily — refresh Kaito reference + rebuild our roster + reputation weights
python -m scripts.scrape_kaito --vertical all && \
python -m app.mindshare.roster --vertical crypto && \
python -m app.mindshare.weights --vertical crypto
```

(The worker also rebuilds roster+weights every `MINDSHARE_ROSTER_REFRESH_TICKS`, but the Kaito
*scrape* — the source — is this separate job, so a scrape hiccup never stalls the hourly tick.)

## Env

Shared: `DATABASE_URL=postgresql+asyncpg://…`, `LOUDRR_GATEWAY_API`, `GATEWAY_BASE_URL`.

| var | for | default |
|---|---|---|
| `MINDSHARE_VERTICALS` | worker | `crypto` (`crypto,ai,trading` for all) |
| `MINDSHARE_TICK_SECONDS` | worker | `3600` |
| `MINDSHARE_MAX_PAGES` | worker | `2` (tweets/account/tick) |
| `MINDSHARE_ROSTER_REFRESH_TICKS` | worker | `24` |
| `KAITO_PROXIES` | scrape job | — (data/ is gitignored, so set the Webshare `host:port:user:pass` lines here; newline/comma/semicolon separated). The **worker needs no proxies** — it only uses the gateway. |
| `CORS_ORIGINS` | api | `*` (set to the loudrr web origin to lock down) |

## First-deploy bootstrap

1. Point `DATABASE_URL` at Postgres; run the **scheduled job** once (populates `kaito_mindshare`,
   `ms_roster`, weights).
2. Start the **`mindshare` worker** — it ingests tweets and writes hourly `ms_snapshot`/`ms_mover`.
3. Start/redeploy the **`api`** — `GET /v1/mindshare` lists what's live; the loudrr web app reads
   `/v1/mindshare/crypto/ALL?window=7d` and `/v1/mindshare/crypto/ALL/movers`.

## Cost / scale
Hourly tick ≈ a few cents (incremental, `since_id`). Full crypto roster ≈ $0.43 cold, cents/hour
warm. Aggregation reads hourly buckets, not raw tweets, so it stays cheap as volume grows. Public
API is read-only off pre-aggregated `ms_snapshot`/`ms_mover` (cache-friendly).

## Accuracy
Calibrated to Kaito (log engagement + 48h recency) → crypto/ALL 9/10, EXCHANGE 8/10 top-10 overlap.
Guarded by `tests/mindshare/test_parity_kaito.py` (`OVERLAP_MIN`). Re-tune any time with
`python -m app.mindshare.calibrate`. PRETGE/small-caps need cashtag-only attribution (see ARCHITECTURE.md).
