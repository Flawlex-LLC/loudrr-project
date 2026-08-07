from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.admin_panel import mount_admin
from app.api import (
    admin as admin_api,
    claims,
    feature_interest,
    posts,
    sessions,
    site_settings,
    users,
    waitlist,
    x_verification,
)
from app.core.config import settings
from app.core.deps import get_current_user
from app.core.exception_handlers import register_exception_handlers
from app.core.limiter import limiter
from app.core.request_context import RequestContextMiddleware
from app.db.session import engine, SessionLocal, get_session
from app.models.user import User
from app.services.site_settings import get_setting
from app.services.tier import load_tiers_from_settings

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.connect() as conn:
        # create_all is removed since alembic is used for migrations,
        # and it can cause issues with the migration process.
        # we can ensure that the database connection is working
        # by executing a simple query.
        # await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("SELECT 1"))
    print("Database connection: OK")
    if settings.debug:
        # the ?telegram_id= auth bypass is active in debug — MUST be off in prod
        print("WARNING: DEBUG=True — Telegram auth bypass is ENABLED. Never run prod like this.")
    # rebuild the in-memory tier bands from any TIER_* SiteSetting overrides;
    # a failure here is non-fatal — fall back to the hardcoded defaults so the
    # app can still boot during a partial migration / empty-DB scenario.
    try:
        async with SessionLocal() as _tier_db:
            await load_tiers_from_settings(_tier_db)
    except Exception as e:  # pragma: no cover — defensive
        print(f"WARNING: load_tiers_from_settings failed at startup: {e!r} — using defaults")

    # Initialize the error-tracking SDK exactly once, gated on a DSN being
    # configured. sentry-sdk speaks both Sentry's and GlitchTip's protocols
    # — same DSN format, same auto-instrumentation. send_default_pii=False
    # so we never leak telegram_id / x_username into error reports.
    dsn = settings.glitchtip_dsn or settings.sentry_dsn
    if dsn:
        import sentry_sdk
        sentry_sdk.init(
            dsn=dsn,
            send_default_pii=False,
            traces_sample_rate=0.1 if not settings.debug else 0.0,
            environment=settings.sentry_environment or ("dev" if settings.debug else "prod"),
        )
        print(f"Error tracking ENABLED: {('GlitchTip' if settings.glitchtip_dsn else 'Sentry')}")
    yield
    # close the shared arq/Redis pool (if one was opened by enqueue())
    from app.tasks.enqueue import close_pool
    await close_pool()


app = FastAPI(title=settings.app_name, lifespan=lifespan)

# CORS — the Next.js frontend proxies same-origin in prod, but allow the
# configured origins for direct/dev calls. CORS_ALLOWED_ORIGINS is comma-sep.
_cors_origins = [o.strip() for o in settings.cors_allowed_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins or ["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def security_headers(request, call_next):
    """Defensive response headers on every response (closes the ZAP baseline
    warnings). Values suit a JSON API plus the one standalone HTML page (the X
    OAuth callback, which uses only inline styles): CSP allows inline style but
    nothing else, and CORP is 'cross-origin' because a separate frontend calls
    this API."""
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault(
        "Permissions-Policy", "geolocation=(), microphone=(), camera=()"
    )
    response.headers.setdefault("Cross-Origin-Resource-Policy", "cross-origin")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'",
    )
    return response


# Request-ID + structlog context binding. Added LAST so it's the OUTERMOST
# middleware — Starlette runs middlewares in reverse-add order, so this
# wraps CORS + security_headers and is alive for every log line emitted
# during a request, including ones from other middlewares + exception
# handlers. Pairs with structlog.contextvars.merge_contextvars in
# app/core/logging.py.
app.add_middleware(RequestContextMiddleware)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]  # slowapi handler signature; runtime is correct
app.include_router(site_settings.router)
register_exception_handlers(app)


@app.get("/health")
def health_check():
    """Liveness probe — proves the process is alive and the event loop responsive.

    Intentionally trivial: does NOT touch the DB / Redis / external services,
    so a transient dep outage (e.g. Postgres failover, Redis restart) won't
    flap the liveness signal and trigger needless pod restarts. For
    dependency health, use /readyz.
    """
    return {"status": "ok"}


@app.get("/readyz")
async def readyz(db=Depends(get_session)):
    """Readiness probe — pings the dependencies the API ACTUALLY needs to serve
    traffic. Returns 200 + per-check dict on success, 503 + dict on any failure.

    Coolify / k8s should route this to the readiness signal: when one of the
    deps is degraded the orchestrator stops sending NEW traffic to this pod
    but leaves /health (liveness) alone so the existing requests can drain.

    Checks:
      - db    : SELECT 1 against Postgres (always)
      - redis : PING against Redis IF settings.redis_url is set (otherwise
                skipped — local dev with no queue is a valid topology)
    """
    from starlette.responses import JSONResponse

    checks: dict[str, str] = {}
    ok = True

    # DB — required. Fail loudly if Postgres is unreachable.
    try:
        await db.execute(text("SELECT 1"))
        checks["db"] = "ok"
    except Exception as e:  # pragma: no cover — depends on live infra
        checks["db"] = f"error: {type(e).__name__}: {e}"
        ok = False

    # Redis — optional. Only check when the queue is wired.
    if settings.redis_url:
        try:
            import redis.asyncio as aioredis  # type: ignore[import-untyped]

            # 5s connect + 3s op — 2s was tripping when Docker was cold-starting
            # (Redis was healthy on PING from the shell but the async client's
            # first connect attempt hit the ceiling). Still small enough that a
            # real outage flips the probe to 503 within one Coolify health check.
            client = aioredis.from_url(
                settings.redis_url, socket_timeout=3, socket_connect_timeout=5
            )
            await client.ping()
            await client.aclose()
            checks["redis"] = "ok"
        except Exception as e:  # pragma: no cover — depends on live infra
            checks["redis"] = f"error: {type(e).__name__}: {e}"
            ok = False
    else:
        checks["redis"] = "skipped (REDIS_URL unset)"

    if ok:
        return checks
    return JSONResponse(content=checks, status_code=503)


# Public mini-app settings (the frontend fetches this once on load to know the
# post-cost range). Matches the Django contract path /api/miniapp/settings/.
@app.get("/settings/")
async def miniapp_settings(db=Depends(get_session)):
    return {
        "post_cost_min": await get_setting(db, "POST_COST_MIN"),
        "post_cost_max": await get_setting(db, "POST_COST_MAX"),
    }


@app.get("/whoami")
async def whoami(user: User = Depends(get_current_user)):
    return {"UUID": user.id, "username": user.telegram_username}


app.include_router(waitlist.router)
app.include_router(users.router)
app.include_router(x_verification.router)
app.include_router(sessions.router)
app.include_router(claims.router)
app.include_router(posts.router)
app.include_router(feature_interest.router)
# privileged admin API (RBAC-gated; service-backed). Distinct from the SQLAdmin
# UI at /admin — this one is for the Next.js admin dashboard and CLI tools.
app.include_router(admin_api.router)

# the SQLAdmin operations panel at /admin (separate admin login)
mount_admin(app)