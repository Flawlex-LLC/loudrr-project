# Coolify deploy — post-provisioning handoff

Everything below was created on `server1.flawlex.co` via the Coolify API on
2026-08-08. **UPDATE 2026-08-12: first real deploy happened — see "Deploy-night
learnings" at the bottom for what actually broke and how it's configured now.**

## What's live in Coolify already

**Project**: `loudrr` (`pkong921t9zpiepmkv41po4n`) → environment `production` (`vspfouxpny2xqummx27hxw1t`)

**Applications** (5, all pointed at `Flawlex-LLC/loudrr-project@main`):

| Name                     | UUID                       | base_directory                | Port | Dockerfile         | Auto-FQDN (sslip.io)                                        |
| ------------------------ | -------------------------- | ----------------------------- | ---- | ------------------ | ----------------------------------------------------------- |
| loudrr-backend           | uy5o2whvgglns6bxge30u3cl   | /loudrr-fastapi/backend       | 8000 | Dockerfile         | http://uy5o2whvgglns6bxge30u3cl.204.168.248.251.sslip.io    |
| loudrr-worker            | pou7c6bbd38450pr78b9g1pu   | /loudrr-fastapi/backend       | 8000 | Dockerfile         | http://pou7c6bbd38450pr78b9g1pu.204.168.248.251.sslip.io    |
| loudrr-frontend          | ucp750s8c1q2a4a0343aqnr7   | /loudrr-fastapi/frontend      | 3000 | Dockerfile         | http://ucp750s8c1q2a4a0343aqnr7.204.168.248.251.sslip.io    |
| loudrr-analytics-api     | snmbna1wikpt1tz4z00jlnfm   | /loudrr-analytics-service     | 8001 | Dockerfile         | http://snmbna1wikpt1tz4z00jlnfm.204.168.248.251.sslip.io    |
| loudrr-analytics-scrape  | d1djjxnyiatreslmfy9it15y   | /loudrr-analytics-service     | 8001 | Dockerfile.scrape  | http://d1djjxnyiatreslmfy9it15y.204.168.248.251.sslip.io    |

**Databases** (both auto-generated, using internal docker hostnames for
inter-container comms — no public port):

| Service         | UUID                       | Internal URL                                                                                                                                             |
| --------------- | -------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Postgres 16     | ms0q1gaxez5paxfnl5yjm08w   | `postgresql+asyncpg://loudrr:loudrr_prod_1786148775@ms0q1gaxez5paxfnl5yjm08w:5432/loudrr`                                                                |
| Redis 7         | a25xzfeqa1z0zb619yvjxps4   | `redis://default:bJ8EvKjjaKLDg0haplr10Nv1OAP7bncuVvEoycbHjeSDn9m6URokfhOcq7hMwTwN@a25xzfeqa1z0zb619yvjxps4:6379/0`                                       |

**Env vars seeded** (65 total via API):
- Backend + worker: 29 each (DATABASE_URL / REDIS_URL / gateway keys / TG token / X OAuth / secrets / SITE_URL placeholder)
- Frontend: 3 (BACKEND_ORIGIN → internal backend hostname, NEXT_PUBLIC_ADMIN_API_URL, NODE_ENV)
- Analytics × 2: 2 each (GATEWAY_BASE_URL, LOUDRR_GATEWAY_API)

## Secrets (SAVE THESE — I can't retrieve them later)

Auto-generated at seed time. Ideally you'll rotate to your own values in the Coolify
env-vars UI, but until then these are what's live:

```
SECRET_KEY       = -hrD1bsy50PWuvi0ABoDJACrlVMRlnPtJNF0j9YzPDUZL1QW2Twn9cnMGa1I7IhR
ADMIN_PASSWORD   = NDsgdXuaGsbuvYKxxMIQaTTw
ENCRYPTION_KEY   = 0779093af386e2a522747d3144d6b688
```

## Before you hit deploy — final 5 clicks

1. **Attach real domains** (in each app's `Configuration → Domains`):
   - loudrr-backend  → `api.loudrr.com` (or `dev-api.loudrr.com` for staging)
   - loudrr-frontend → `app.loudrr.com` (or `dev-app.loudrr.com`)
   - loudrr-analytics-api → internal-only OK, or `analytics.loudrr.com`
   - loudrr-worker + loudrr-analytics-scrape → NO domain needed (background workers)

2. **Update the placeholder env vars** in loudrr-backend + loudrr-worker
   (`Configuration → Environment Variables`) — these currently point at the
   dev-api/dev-app hostnames:
   - `SITE_URL` → `https://api.loudrr.com`
   - `X_OAUTH_CALLBACK_URL` → `https://api.loudrr.com/api/auth/x/callback/`
   - `MINIAPP_URL` → `https://app.loudrr.com/app`
   - `CORS_ALLOWED_ORIGINS` → your real frontend origins

3. **Flip prod-guard on** (in loudrr-backend + loudrr-worker):
   - Set `ENVIRONMENT=prod`  (was empty for the seed so the guard didn't fire
     against placeholder values)
   The guard will refuse boot if `DEBUG=True` OR `SECRET_KEY` is the dev default
   OR `ADMIN_PASSWORD` is blank OR `TELEGRAM_BOT_TOKEN` is blank — all four are
   already correct.

4. **Rebuild frontend** (build-time env change) — after setting `BACKEND_ORIGIN`
   to the real domain, hit `Deploy → Force Rebuild` on loudrr-frontend so
   Next.js bakes the new origin into the client bundle.

5. **Deploy each app** — click `Deploy` on:
   - loudrr-backend  (first — it runs alembic on boot; wait for /readyz to hit 200)
   - loudrr-analytics-api  (parallel with backend)
   - loudrr-frontend
   - loudrr-worker
   - loudrr-analytics-scrape  (last — starts crawling)

## Post-deploy smoke test

```bash
# backend readyz — should return {"db":"ok","redis":"ok"}
curl https://api.loudrr.com/readyz

# analytics — should return the API status page
curl https://analytics.loudrr.com/  # or the sslip URL

# frontend — should render the landing / redirect to /app
curl -I https://app.loudrr.com/
```

## Rollback (if a deploy breaks)

Coolify keeps the previous container running until the new build succeeds, so a
broken build is self-mitigating. To manually roll back to a prior deploy:
`Configuration → Deployments → <older commit> → Redeploy`.

To reset the whole monorepo attempt: the `archive/pre-monorepo-fastapi` branch
on `Flawlex-LLC/loudrr-project` has your old fastapi tip, and the archived
`Flawlex-LLC/loudrr-analytics-service` repo has the analytics history.

## Where the API calls came from

`scripts/coolify-migrate.sh` in this repo has the seed script (the version that
worked — earlier attempts hit Cloudflare WAF 1010 without a browser UA, and
tried to pass a non-existent `is_build_time` field). Adapt it for future
bulk-create / bulk-update passes against Coolify's API.

## Deploy-night learnings (2026-08-12 — first real deploy)

Everything below is LIVE state, discovered/fixed during the first deploy:

1. **loudrr-db + loudrr-redis were stopped** — Coolify DB resources don't
   auto-start. `GET /api/v1/databases/{uuid}/start` brings them up.

2. **loudrr-db was empty** (no schema). Bootstrapped via: local create_all +
   `alembic stamp head` + seed_settings + seed_admins into a scratch DB,
   `pg_dump` that, apply the SQL to the prod DB. Don't run bare
   `alembic upgrade head` on a FRESH db — the chain breaks at `d1e2f3a4b5c6`
   (duplicate `credits_non_negative` constraint). Stamped DBs upgrade fine.

3. **Frontend build failed on Coolify but not locally**: Coolify injects every
   buildtime env var as Dockerfile ARG/ENV — including `NODE_ENV=production` —
   so `npm ci` skipped devDependencies and `next.config.ts` couldn't load
   (no typescript). Fixed in the Dockerfile: `npm ci --include=dev`.

4. **analytics-api needs `RUN_MODE=api`** or the image's CMD falls through to
   the crawler (`scripts/run_until_done.py`). It also listens on **8000**, not
   8001 — `ports_exposes` and every consumer's `LOUDRR_ANALYTICS_URL` now say
   `http://snmbna1wikpt1tz4z00jlnfm:8000`.

5. **Env-var changes need a full re-DEPLOY, not "Restart"** — restart reuses
   the old container with its baked-in env.

6. **Analytics data**: restored from the 9.4GB Contabo dump
   (`/root/loudrr-analytics-20260808-161912.dump.gz` on the Hetzner box) into
   the postgres:18 Coolify DB (`rdkc0cdia7vhxyq4e7538931`), database
   `loudrr_analytics` (~65GB restored: edges 32GB + eng_tweet_raw 30GB).
   `DATABASE_URL` is set on both analytics apps. Contabo (`api.loudrr.com`)
   is dead — 503, SSH refused.

7. **dev-api/dev-app.loudrr.com 530**: these are Cloudflare Tunnel routes and
   the tunnel daemon ran on the dead Contabo box. `cloudflared` 2026.7.3 is
   pre-installed on the Hetzner box; finish with
   `cloudflared service install <tunnel-token>` (token from CF Zero Trust
   dashboard), or move the DNS records to point at 204.168.248.251 directly.

8. **X OAuth for the waitlist flow** needs
   `https://dev-api.loudrr.com/api/auth/x/callback/waitlist/` registered in
   the X developer portal (the waitlist-specific callback,
   `X_OAUTH_WAITLIST_CALLBACK_URL`, is already set on backend + worker).
