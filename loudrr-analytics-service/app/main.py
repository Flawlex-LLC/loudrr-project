"""FastAPI entrypoint for loudrr-analytics-service.

The crypto influence-scoring layer (Sorsa-style) the loudrr landing page connects to.
Crawl/seed/score are run as jobs (see scripts/), not in the request path.
"""
import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.core.config import settings
from app.engagement.api import router as engagement_router
from app.engagement.hooks import router as hooks_router
from app.engagement.live import router as live_router
from app.mindshare.api import router as mindshare_router

logging.basicConfig(level=settings.log_level)
logger = logging.getLogger("loudrr.main")


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Start the realtime KOL wallet watcher IN THIS PROCESS.

    It must share the API server's in-process fan-out hub (app/engagement/live.py) — its
    ride broadcasts only reach browsers connected to the same process's WebSocket. Running it
    in the separate engagement worker would broadcast into a hub no browser is attached to.
    No-op unless WALLET_WATCH_ENABLED, so import + startup stay free by default.

    (Scale note: with uvicorn --workers N each process runs its own watcher + hub, so a ride
    caught by worker A won't reach a browser pinned to worker B. Single-process today; the
    scale-out is a Redis-backed hub — tracked in memory, not needed at current volume.)
    """
    task: asyncio.Task | None = None
    if settings.wallet_watch_enabled:
        from app.engagement.live_wallets import run_wallet_watcher
        task = asyncio.create_task(run_wallet_watcher())
        logger.info("wallet watcher task started")
    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


app = FastAPI(
    title="loudrr-analytics-service",
    description="Crypto X influence scoring (Sorsa-style) on twitterapi.io",
    version="0.1.0",
    lifespan=lifespan,
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
# Loudrr Mindshare — separate service/package, mounted read-only (app/mindshare/).
app.include_router(mindshare_router, prefix="/v1/mindshare")
# Smart Engagement — separate service/package (app/engagement/); degrades, never 500s.
app.include_router(engagement_router, prefix="/v1")
# Realtime fan-out (WS /v1/kol-calls/live). WebSockets bypass CORSMiddleware entirely — the
# handshake carries no Origin preflight — so the GET-only allowlist above doesn't gate it.
app.include_router(live_router, prefix="/v1")
# Gateway X push -> realtime KOL calls. The ONE write endpoint here: secret-authenticated
# and fail-closed (app/engagement/hooks.py). CORS stays GET-only on purpose — this is a
# server-to-server callback, never called from a browser, so no origin needs to reach it.
app.include_router(hooks_router, prefix="/v1")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "env": settings.app_env}
