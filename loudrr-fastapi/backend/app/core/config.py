from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # extra="ignore" so a stale variable in .env (e.g. TWITTER_API_KEY left over
    # after the gateway migration) doesn't crash boot — we log unknown vars once
    # at import time via env_file so ops can spot them, but they don't gate
    # startup. Without this, Pydantic-Settings defaults to extra="forbid".
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")
    # app wireup
    app_name: str = "Loudrr"
    # debug default to false, can be overridden by .env
    debug: bool = False
    # db url
    database_url: (
        str  # no default — required; Pydantic errors at startup if .env lacks it
    )
    items_per_page: int = 20

    # --- DB connection pool (scale; see backend/tests/SCALING.md) ---
    # Per-process pool. Across N web + M worker processes, keep
    # N+M × (db_pool_size + db_max_overflow) under Postgres max_connections,
    # or front it with PgBouncer (transaction pooling).
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_timeout: int = 30      # seconds to wait for a free connection before erroring
    db_pool_recycle: int = 1800    # recycle a connection after 30 min (avoid stale handles)

    # --- Telegram ---
    # the bot's secret token (a string like "123456:ABC-…"), read from .env
    telegram_bot_token: str = ""
    telegram_bot_username: str = ""

    # --- Redis / task queue (Ch16) ---
    redis_url: str = ""
    # when True, on-demand jobs enqueue to arq (needs Redis + a running worker);
    # when False (dev/test), they run via FastAPI BackgroundTasks in-process
    use_task_queue: bool = False

    # --- External services (Ch10/11/13/15) ---
    # Loudrr gateway (Ch13 reply verification). Our own drop-in twitterapi.io-
    # compatible service — the ONLY upstream. No fallback to twitterapi.io.
    # If loudrr_gateway_api is blank, verify_reply returns the benefit-of-the-
    # doubt "skipped+passed" result (spec §0 #8, §5.2).
    loudrr_gateway_api: str = ""
    gateway_base_url: str = "https://gateway.loudrr.com"

    # Base URL of the loudrr-analytics-service. This is the ONLY score provider
    # — no TweetScout fallback exists. Empty -> graceful "default score, retry
    # later" (users are never punished for our infra being down; scores just
    # return None and callers use the default).
    loudrr_analytics_url: str = ""
    # Optional shared secret sent as X-API-Key on requests to the analytics
    # service. Required by the analytics side (as ANALYTICS_API_KEY) for the
    # miniapp-facing endpoints: /v1/score, /v1/top-followers, /v1/score-changes,
    # /v1/followers-stats, /v1/top-following. Leave empty for local dev against
    # a keyless analytics instance; set in prod to hit the gated endpoints.
    loudrr_analytics_key: str = ""

    # --- X OAuth 2.0 (Ch11) ---
    x_oauth_client_id: str = ""
    x_oauth_client_secret: str = ""
    x_oauth_callback_url: str = ""

    # --- URLs / misc ---
    site_url: str = ""
    landing_url: str = ""
    # Telegram WebApp deep-link (the mini-app). Used as the WebApp URL of the
    # "Open Loudrr" inline-keyboard button on waitlist_submitted / waitlist_approved
    # cards (parity with Django bots/telegram/notifications.py:43-46). When unset,
    # outbox dispatch sends the message without an inline keyboard.
    miniapp_url: str = ""
    encryption_key: str = ""           # 32-byte key for redirect-URL encryption
    # comma-separated; default is the canonical dev admin (Oxblest,
    # telegram_id=6451704338) — matches the Django reference's default.
    # In prod set ADMIN_TELEGRAM_IDS in .env to your real admin IDs.
    admin_telegram_ids: str = "6451704338"
    cors_allowed_origins: str = ""     # comma-separated

    # --- admin panel (Ch17) ---
    secret_key: str = "dev-insecure-secret-change-me"  # session signing for SQLAdmin
    admin_username: str = "admin"
    admin_password: str = ""           # set in prod; blank disables admin login

    # --- observability ---
    # DEBUG, INFO, WARNING, ERROR, CRITICAL. INFO is sensible in prod; flip to
    # DEBUG locally when you need to see SQLAlchemy echo + asyncio internals.
    log_level: str = "INFO"

    # --- error tracking (Sentry / GlitchTip) ---
    # Both fields accept the same DSN format (sentry-sdk speaks both). When
    # ONE is set the SDK initializes at startup and the request-id middleware
    # explicitly captures unhandled exceptions on the way through. When BOTH
    # are unset the SDK never initializes — no overhead in dev.
    # Set GLITCHTIP_DSN to point at your self-hosted GlitchTip; SENTRY_DSN
    # is the same shape if you switch to Sentry-hosted later.
    sentry_dsn: str = ""
    glitchtip_dsn: str = ""
    # Comma-separated environments. Defaults to deriving from debug: 'dev'
    # when debug=True, else 'prod'. Override to 'staging' etc. in .env.
    sentry_environment: str = ""

    # --- deployment environment (prod-guard) ---
    # One of: "dev" | "staging" | "prod". When set to "prod" the app REFUSES
    # to boot if any dev-only footgun is active (debug=True, blank secret,
    # blank admin_password). Set ENVIRONMENT=prod in your prod .env — the
    # default "" leaves the guard off so local dev is frictionless.
    environment: str = ""


# business logic settings
settings = Settings()  # type: ignore[call-arg]  # pydantic-settings reads required fields from .env at runtime


# --- prod-guard: fail fast on dev-only settings shipped to prod ---
# The `?telegram_id=` auth bypass, the default insecure SECRET_KEY, and a
# blank ADMIN_PASSWORD are all safe locally but catastrophic in prod. Rather
# than trust "we set it right in the .env" and hope, refuse to boot when
# ENVIRONMENT=prod combines with any of them. Ops sees a clear crash instead
# of a silently-vulnerable service.
if settings.environment == "prod":
    _prod_errors: list[str] = []
    if settings.debug:
        _prod_errors.append("DEBUG=True — the ?telegram_id= auth bypass is active. Set DEBUG=False.")
    if settings.secret_key == "dev-insecure-secret-change-me":
        _prod_errors.append("SECRET_KEY is the dev default — set a real 32+ char secret.")
    if not settings.admin_password:
        _prod_errors.append("ADMIN_PASSWORD is empty — SQLAdmin login is disabled entirely.")
    if not settings.telegram_bot_token:
        _prod_errors.append("TELEGRAM_BOT_TOKEN is empty — HMAC verification cannot run, all auth will 401.")
    if _prod_errors:
        raise RuntimeError(
            "ENVIRONMENT=prod but the following dev-only settings are still active — refusing to boot:\n  - "
            + "\n  - ".join(_prod_errors)
        )

# Configure structured logging the moment Settings is built — every subsequent
# `logging.getLogger(__name__)` call across the codebase will route through
# structlog automatically (pretty key=val in dev, single-line JSON in prod).
# Done here (not in main.py) so test imports + arq workers + scripts/* also
# get the new format without each having to remember to call it.
from app.core.logging import configure_logging  # noqa: E402  — must come after settings is built
configure_logging(debug=settings.debug, log_level=settings.log_level)
