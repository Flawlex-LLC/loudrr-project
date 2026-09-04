"""FastAPI entrypoint for loudrr-analytics-service.

ONE job: score X accounts. An X account's Loudrr Score is the weighted sum of
the PageRank-ranked "smart set" members who follow it — computed offline by the
crawl/seed/score jobs in scripts/, served read-only from Postgres here. Nothing
is scraped in the request path.

Scope note (2026-08): the mindshare, smart-engagement/KOL-calls and vendor-parity
subsystems were removed — this service is scoring only. Git history has them if
they ever come back.
"""
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.core.config import settings

logging.basicConfig(level=settings.log_level)
logger = logging.getLogger("loudrr.main")


app = FastAPI(
    title="loudrr-analytics-service",
    description="X influence scoring — the Loudrr Score",
    version="0.1.0",
)
# Public read-only API — the loudrr web app + funnel call it cross-origin. CORS_ORIGINS is a
# comma-separated allowlist; default "*" is safe here (GET-only, no cookies/credentials).
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",")] if settings.cors_origins else ["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)
app.include_router(router, prefix="/v1")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "env": settings.app_env}
