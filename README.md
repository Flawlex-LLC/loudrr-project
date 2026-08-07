# loudrr-project

Monorepo for the Loudrr product. Three services live here, each in its own
subdirectory with its own Dockerfile, tests, dependencies, and README.

## Layout

```
loudrr-project/
├── loudrr-fastapi/           # FastAPI backend + Next.js miniapp/admin frontend
│   ├── backend/              # Python 3.14, FastAPI, PostgreSQL, arq/Redis
│   ├── frontend/             # Next.js 16, Tailwind v4, Telegram Mini App SDK
│   ├── scripts/              # dev.ps1, dev-stop.ps1 (Windows Terminal launcher)
│   └── docs/
├── loudrr-analytics-service/ # Loudrr Score pipeline (X influence graph)
│   ├── app/                  # scoring API, engagement crawler, mindshare
│   ├── scripts/              # ~50 CLI: crawl, score, calibrate, harvest, backfill
│   ├── web/                  # Next.js dashboard for analytics
│   └── docs/
└── coming-soon/              # Pre-launch landing (Next.js, Vercel-deployed)
```

## Related infra (NOT in this repo)

- **`gateway.loudrr.com`** — our own twitterapi.io-compatible service. Fronted separately;
  both loudrr-fastapi and loudrr-analytics-service call it via `LOUDRR_GATEWAY_API`.

## Deployment

| Service            | Where                        | Build context                                |
| ------------------ | ---------------------------- | -------------------------------------------- |
| Backend (FastAPI)  | Coolify                      | `./loudrr-fastapi/backend`                   |
| Frontend (Next.js) | Coolify                      | `./loudrr-fastapi/frontend`                  |
| Analytics API      | Coolify                      | `./loudrr-analytics-service`                 |
| Analytics workers  | Coolify (per RUN_MODE)       | `./loudrr-analytics-service`                 |
| Coming-soon        | Vercel (root: `coming-soon`) | `./coming-soon`                              |

## Historical branches

- `main`             — active monorepo
- `landing`          — legacy standalone coming-soon (superseded; coming-soon now on main)
- `archive/django`                     — original Django implementation (frozen)
- `archive/pre-monorepo-fastapi`       — loudrr-fastapi tip at monorepo cut (12 commits)
- `archive/pre-monorepo-analytics`     — analytics service tip at monorepo cut (also on `Flawlex-LLC/loudrr-analytics-service` — that repo is archived read-only)

## Local development

Each service has its own dev workflow — see the per-service README. The
convenience launcher is `loudrr-fastapi/scripts/dev.ps1` which spins up
Postgres + Redis + uvicorn + arq + Next.js + cloudflared tunnel in one
Windows Terminal window.

## Support

Telegram: [@ace_flawlex](https://t.me/ace_flawlex)
