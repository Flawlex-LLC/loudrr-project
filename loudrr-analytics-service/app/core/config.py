"""Runtime configuration, loaded from environment / .env.

Single source of truth for keys, storage URLs, and the crawl/scoring knobs that
drive cost (see docs/cost_model.md). Everything is overridable via env so a crawl
can be re-budgeted without code changes.
"""
from functools import lru_cache

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- API keys ---
    twitterapi_io_key: str = ""
    # loudrr gateway — our own drop-in twitterapi.io-compatible API (verified 2026-06-18:
    # identical paths, params, response shapes, and x-api-key auth; own credit economics).
    # Preferred over api.twitterapi.io whenever set, so the crawl routes through us.
    loudrr_gateway_api: str = ""
    gateway_base_url: str = "https://gateway.loudrr.com"
    # CoinGecko Demo API key (free tier, ~30 calls/min). Header: x-cg-demo-api-key.
    coingecko_demo: str = Field(
        default="", validation_alias=AliasChoices("COINGECKHO_DEMO", "COINGECKO_DEMO_KEY", "COINGECKO_DEMO")
    )
    # Our own INCOMING key: gates the miniapp-facing endpoints (/v1/score,
    # /v1/top-followers, /v1/score-changes, /v1/followers-stats,
    # /v1/top-following). Unset = keyless mode (historical behavior — safe
    # for dev + smooth rollout). Set in prod so only the miniapp with the
    # matching env var can hit these endpoints. See app/api/auth.py.
    analytics_api_key: str = ""

    # --- Storage ---
    database_url: str = (
        "postgresql+asyncpg://postgres:postgres@localhost:5432/loudrr_analytics"
    )
    redis_url: str = "redis://localhost:6379/0"

    @field_validator("database_url")
    @classmethod
    def _normalize_db_url(cls, v: str) -> str:
        """SQLAlchemy 2.x rejects the bare ``postgres://`` scheme, and our engine is async,
        so coerce any plain Postgres URL onto the asyncpg driver. Common in hosted env vars
        (Coolify/Heroku set ``postgres://``). Leaves sqlite/other URLs untouched."""
        if v.startswith("postgres://"):
            return "postgresql+asyncpg://" + v[len("postgres://"):]
        if v.startswith("postgresql://"):  # has scheme but no +driver
            return "postgresql+asyncpg://" + v[len("postgresql://"):]
        return v

    # --- Crawl knobs (drive cost) ---
    # Profiles-only path: /user/followings returns full profiles, max 200/page.
    # (No bulk following-IDs endpoint exists in the verified catalog.)
    crawl_profile_page_size: int = 200
    # Bulk follow-IDs endpoint (gateway /user/followings_ids): 5,000 IDs/page, no truncation,
    # ~$0.0049/1k. IDs are all the edge graph needs — the default crawl path now.
    crawl_ids_page_size: int = 5000
    # Per-member following ceiling. UNCAPPED — the bulk-IDs endpoint returns 5,000/page at
    # ~$0.0049/1k, so even a 1M-follower account is ~200 cheap requests; there's no longer a
    # cost/speed reason to cap. Get every following. (Client's cursor-not-advancing guard +
    # member-retry/defer still bound any pathological runaway.) Validator targets full count.
    crawl_max_following_per_member: int | None = None
    # Verify each member's crawl against their REAL following count instead of trusting the
    # endpoint's has_next_page. The gateway intermittently claims the list is finished when it
    # isn't (no error — it just stops), which silently truncated members: @JohnCena stored with
    # 55k of 1.06M following, @FerreWeb3 with 78% of his. A member is only marked crawled when
    # we hold >= crawl_complete_at of their real count; otherwise it's retried/deferred.
    crawl_verify_completeness: bool = True
    # 0.90, not 1.0: X's `following` count includes suspended/deleted/protected accounts that
    # the API legitimately won't return, so a perfectly-crawled member can land a few % short.
    crawl_complete_at: float = 0.90
    # Per-member IN-RUN retry budget. A member is retried this many times (with backoff, on
    # top of the client's per-page transient retries) before being DEFERRED to the next pass
    # — it is NEVER silently skipped, and the job isn't "done" until every member succeeds.
    crawl_member_max_retries: int = 10
    # Members crawled in parallel. All workers share the ONE client QPS limiter, so the global
    # request rate stays capped at crawl_qps — concurrency just fills the per-request wait time
    # (the crawl is latency-bound, not rate-bound) and stops a heavy-follower blocking the line.
    crawl_concurrency: int = 20        # gateway team's recommended concurrency (2026-06-21)
    crawl_qps: int = 20
    # Gateway team guidance (2026-06-21): ~20-40 req/min + HONOR Retry-After. Window-based leaky
    # bucket keeps us in range (30/min); the client honors any Retry-After header on 429/503 so
    # their new rate-mechanism can pace us directly instead of us blindly retrying.
    crawl_rate_calls: int = 30
    crawl_rate_window_s: int = 60
    crawl_daily_budget_usd: float = 50.0

    # --- Scoring knobs ---
    pagerank_alpha: float = 0.85
    pagerank_max_iter: int = 100
    score_display_scale: float = 1000.0
    # Cut the smart set to its top-N voters by pr_rank at score time (None = use all ~98k, the
    # historical behavior). Empirically the bottom tail (rank 40k-98k) adds noise vs Sorsa/
    # TwitterScore, so a 40000 cut slightly sharpens agreement while defining a cleaner "smart
    # followers" universe. Off by default; set SMART_SET_CUTOFF=40000 to activate.
    smart_set_cutoff: int | None = None

    # --- Service ---
    app_env: str = "dev"
    log_level: str = "INFO"
    # Public API CORS allowlist (comma-separated). Blank/"*" = allow any origin (GET-only API).
    cors_origins: str = "*"

    # twitterapi.io credit economics (1 USD = 100k credits), used for budget guards
    credits_per_usd: int = 100_000
    # loudrr gateway credit economics. As of 2026-06-18 the gateway pricing was aligned
    # to twitterapi.io (1 USD = 100k credits), so this matches credits_per_usd. Used only
    # for human-facing USD display; the crawl budget guard is credit-native (exact).
    gateway_credits_per_usd: float = 100_000.0


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
