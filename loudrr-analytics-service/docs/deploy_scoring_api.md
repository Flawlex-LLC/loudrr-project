# Deploy the Loudrr Scoring API (`api.loudrr.com`)

**Goal:** stand up the public, keyless scoring API. The code is done and verified on `main`; this
is purely a Coolify deploy of the existing FastAPI app + one DNS record + a frontend env flip.

The API is the **same repo/image** as the existing `loudrr-analytics-crawl` worker — only the
**start command** differs (uvicorn instead of the crawl). The repo `Dockerfile` already installs
FastAPI/uvicorn and notes: *"Override the command in Coolify to run the API later."*

---

## What it serves

- `GET /health` → `{"status":"ok"}`
- `GET /v1/score?userName=<handle>` → the public Loudrr Score, e.g.
  ```json
  { "userName":"elonmusk", "userId":"44196397", "score":5859,
    "raw":265002.4, "smart_followers":55600, "parity_score": 5780.2 }
  ```
  - `score` = calibrated **Loudrr Score (0–6000)** — the headline number (locked calibration in
    `app/core/loudrr_score.py` + `data/loudrr_calibration.json`).
  - Reads entirely from the precomputed DB index (no scraping in the request path).
  - Also accepts Sorsa-style params: `?username=`, `?user_id=`, `?user_link=`.

Sanity targets once live: `elonmusk≈5859`, `BarackObama≈3507`, `waleswoosh≈3040`, `0xblest_≈607`.

---

## 1. Create the Coolify app

Coolify: **https://server2.flawlex.co**

This is a **dedicated app** (separate from the crawl worker — one app per concern). Must be
created in the **Coolify UI**: the API can't create it because the repo is private (Coolify's API
only exposes the public GitHub source). Easiest = **Clone** `loudrr-analytics-crawl` (already wired
to the private GitHub App), then change the fields below. The crawl app stays as its own app.

The Docker image runs **one of two modes** based on the `RUN_MODE` env (the Dockerfile `CMD`
dispatches) — so no command override is needed, just set the env:

| Setting | Value |
|---|---|
| Name | `loudrr-analytics-api` |
| Project / Environment | `loudrr-analytics` / `production` |
| Server | `contabo-prod` |
| Source | same private GitHub App → repo `Flawlex-LLC/loudrr-analytics-service`, branch `main` |
| Build pack | **Dockerfile** (base directory `/`) |
| **Env: `RUN_MODE`** | `api`  ← makes the image run uvicorn (default/unset = crawl) |
| **Ports exposes** | `8000` |
| **Health check path** | `/health` |
| **Domain (FQDN)** | `api.loudrr.com` (set it here in the UI so Coolify wires Traefik + the cert) |

Coolify reference UUIDs (if using the API): project `szqm2rox9rzpnvzb18r8bx1r`,
server `raxvfh9rfyw3r7titbuymu04`, environment `fx5tijmy0t2jar9jhgkj7euj`,
crawl app to clone `iemilrgwrjskma83vq0yrtww`.

## 2. Environment variables

If cloned, these carry over — just verify. Otherwise copy from the crawl app:

- **`RUN_MODE=api`** — selects API mode (the Dockerfile `CMD` dispatches; default/unset = crawl).
- **`DATABASE_URL`** — must be the **internal** Postgres (async URL), NOT the public proxy.
  - Use the in-cluster DB hostname (the `loudrr-analytics-db` service / uuid `w1172cwq38kgg2zhbqu7wcuw`) on port **5432**, e.g.
    `postgresql+asyncpg://postgres:<PW>@<internal-db-host>:5432/loudrr_analytics`
  - ⚠️ Do **not** point it at `213.199.54.248:5433` (that public port is throttled/closed; internal is fast).
- **`LOUDRR_GATEWAY_API`** — gateway key (for resolving handles not yet in our DB; never call api.twitterapi.io directly).
- **`APP_ENV=prod`**, **`LOG_LEVEL=INFO`**.

The locked calibration ships in the image (`data/loudrr_calibration.json`); the per-account
scores live in the DB (`smart_set.score`), read at query time — nothing else to provision.

## 3. DNS (Cloudflare)

Add a record for `api.loudrr.com`:
- **A** → `213.199.54.248`
- Set **DNS-only (grey cloud)** at first so Coolify's Let's Encrypt can issue the TLS cert.
  (Can re-enable the orange proxy after the cert is live, if desired.)

## 4. Deploy + verify

Deploy in Coolify, then:
```bash
curl -s https://api.loudrr.com/health
curl -s "https://api.loudrr.com/v1/score?userName=elonmusk"   # score ~5859
curl -s "https://api.loudrr.com/v1/score?userName=waleswoosh" # score ~3040
```

## 5. Point the frontend at it

In the Next.js app (`/web`), set the env and flip the flag:
- `NEXT_PUBLIC_LOUDRR_API=https://api.loudrr.com/v1`
- in `web/lib/api.ts` set `const USE_LIVE = true;`

The frontend already calls `${API}/score?userName=` and reads `data.score`, so no other change.

---

## Notes
- It's read-only against Postgres + light gateway lookups for unknown handles — safe to run
  alongside the crawl worker (separate app, separate process).
- Rollback = stop/delete the app; nothing it does is destructive.
- After any future member **re-score**, re-anchor the calibration: set `A` in
  `data/loudrr_calibration.json` to `Sorsa(Obama)*1.133 / raw(Obama)` and redeploy
  (see `scripts/calibrate_v3.py`).
