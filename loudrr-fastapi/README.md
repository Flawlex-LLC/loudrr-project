# Loudrr

Monorepo for the Loudrr mini-app.

```
loudrr-fastapi/            # rename the folder to this in Explorer (see note)
├── backend/               # FastAPI backend (the v1 API, Ch1–17)
│   ├── app/               # routers, services, models, integrations, tasks
│   ├── alembic/           # migrations
│   ├── tests/             # pytest suite (152 tests)
│   ├── docker-compose.yml # Postgres + Redis + pgAdmin
│   └── .env               # secrets (gitignored)
└── frontend/              # Next.js (shadcn) mini-app
```

## Run it (local dev)

**1. Infra** (from `backend/`):
```
docker compose up -d            # Postgres + Redis
```

**2. Backend** (from `backend/`):
```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\alembic upgrade head
.venv\Scripts\uvicorn app.main:app --reload --port 8000
```

**3. Background worker** (optional, from `backend/`, needs Redis + `USE_TASK_QUEUE=true`):
```
.venv\Scripts\arq app.tasks.worker.WorkerSettings
```

**4. Frontend** (from `frontend/`):
```
npm install
npm run dev                     # http://localhost:3000
```

The frontend calls `/api/miniapp/*`; Next.js rewrites those to the backend
(`next.config.ts`, `BACKEND_ORIGIN`). The admin panel is at `/admin` on the
backend.

## Production notes
- Set `DEBUG=false` in `backend/.env` — the `?telegram_id=` auth bypass is only
  for dev.
- Set `BACKEND_ORIGIN` (frontend) to the deployed backend URL.
- Set `ADMIN_PASSWORD`, `SECRET_KEY`, and real external API keys.
- Docker images, load balancing, and CI/CD (the Ch18–24 production track) are
  not built yet.
