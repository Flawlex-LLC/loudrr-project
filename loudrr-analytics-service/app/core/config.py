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
    # TweetScout == Sorsa (same API). Accept either env name; TWEETSCOUT_API_KEY wins.
    sorsa_key: str = Field(
        default="", validation_alias=AliasChoices("TWEETSCOUT_API_KEY", "SORSA_KEY")
    )
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

    # --- Sorsa/TweetScout harvest knobs (ONE-TIME 10k budget; be conservative) ---
    sorsa_qps: int = 5                 # pace well under their ~20/s to avoid 429s
    sorsa_request_ceiling: int = 9960  # hard stop below the 10k cap (irreversible)

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

    # Engagement ingest source: "advanced_search" (from:<user> include:nativeretweets — 0% foreign
    # overhead, cheaper) or "timeline" (/user/last_tweets by numeric id, includeReplies — ~1.75x
    # cost from thread-context padding, immune to handle renames). Both capture the same edges now
    # that the gateway fixed last_tweets (2026-07-23); default to the cheaper path.
    engagement_source: str = "advanced_search"

    # --- Kaito mindshare scrape (docs/kaito_reverse_engineering.md) ---
    # hub.kaito.ai data API. KEY FINDING: the /voices/* JSON API is NOT Cloudflare-challenged
    # (only the HTML pages are) — so the fast default path is plain curl_cffi + rotating proxies,
    # no browser needed. Datacenter (Webshare) proxies are fine here precisely because there's no
    # CF challenge to fail. Camoufox stays as a fallback if they ever gate the API host.
    kaito_api_base: str = "https://hub.kaito.ai/api/v1"
    # Rotating proxy list (Webshare "h:p:u:pw" lines or http://… lines). Blank = home IP.
    kaito_proxy_file: str = "data/proxies/webshare.txt"
    # Inline proxies for prod (data/ is gitignored, so the file isn't in the container). Set
    # KAITO_PROXIES in Coolify — newline/comma/semicolon-separated "host:port:user:pass" lines.
    kaito_proxies: str = ""
    kaito_concurrency: int = 8          # parallel API calls (burst-tested safe at ~20)
    kaito_impersonate: str = "chrome124"  # curl_cffi TLS/JA3 impersonation profile
    kaito_max_retries: int = 4          # retry 429/5xx/network on a fresh proxy
    kaito_request_pause_ms: int = 0     # extra politeness spacing (0 = rely on concurrency cap)
    # --- Camoufox fallback (only if the API host ever gets CF-gated) ---
    kaito_proxy: str = ""               # single browser proxy "http://user:pass@host:port"
    kaito_headless: bool = False        # CF managed challenge needs a real render context
    kaito_window_w: int = 1200
    kaito_window_h: int = 800

    # --- Smart-engagement tracker (app/engagement/, RUN_MODE=engagement) ---
    # Dedicated system: own pacing + own budget so it can NEVER starve the follower crawl.
    # "seeds" (~3.9k curated) | "top:N" (seeds ∪ top-N by PageRank; prod target top:20000) | "all"
    engagement_universe: str = "seeds"

    @field_validator("engagement_universe")
    @classmethod
    def _validate_universe(cls, v: str) -> str:
        """Fail at BOOT on a malformed value (e.g. 'top:20k'). Validating deep in the data
        path instead would make the worker log-and-sleep forever while looking healthy."""
        u = (v or "seeds").strip().lower()
        if u in ("seeds", "all"):
            return u
        if u.startswith("top:"):
            n = int(u.split(":", 1)[1])  # raises loudly on 'top:20k'
            if n <= 0:
                raise ValueError(f"ENGAGEMENT_UNIVERSE top:N needs N > 0, got {n}")
            return u
        raise ValueError(f"ENGAGEMENT_UNIVERSE must be seeds | top:N | all, got {v!r}")
    engagement_max_pages: int = 3            # ~60 tweets/member/pull (first pull reaches deeper)
    engagement_concurrency: int = 4
    # Its own gateway pacing, BELOW the crawl's 30/min band so both can coexist in the
    # gateway team's ~20-40 req/min guidance.
    engagement_rate_calls: int = 15
    engagement_rate_window_s: int = 60
    engagement_daily_budget_usd: float = 10.0

    # --- Realtime KOL calls (POST /v1/hooks/x, app/engagement/hooks.py) ---
    # Shared secret the gateway must present on every webhook push. This is the ONLY
    # write endpoint we expose to the public internet: unauthenticated, anyone could inject
    # fabricated "KOL called $X" rows straight into the product. So it FAILS CLOSED — blank
    # secret => the hook 503s and pushes are ignored (set LIVE_WEBHOOK_SECRET to enable).
    live_webhook_secret: str = ""

    # --- Realtime KOL wallet watcher (app/engagement/live_wallets.py) ---
    # Inverts the old pool-sampling capture (which watched tokens and hoped a KOL was in the
    # trades — hence eng_ride stayed 0): subscribes to each vault wallet directly via the
    # FREE keyless Solana RPC websocket (logsSubscribe), so EVERY KOL buy is caught regardless
    # of which token it is. Verified 2026-07-16: mainnet-beta accepts 62 subs on one socket,
    # keyless. OFF by default — it opens outbound sockets and does real RPC work.
    wallet_watch_enabled: bool = False
    solana_rpc_url: str = "https://api.mainnet-beta.solana.com"
    solana_ws_url: str = "wss://api.mainnet-beta.solana.com"
    # Cap the watched set (highest Loudrr score first) so getTransaction load stays bounded on
    # the free RPC; 0 = every identity-mapped Solana vault wallet.
    wallet_watch_max: int = 200
    # Subscriptions per socket. The probe took 62 fine; 40 leaves headroom and spreads the
    # notification load across a few connections instead of one hot socket.
    wallet_watch_subs_per_conn: int = 40
    # getTransaction fetchers pulling from the signature queue (dedup'd). The free RPC is
    # rate-limited, so this is small and paced.
    wallet_watch_fetchers: int = 4
    wallet_watch_rpc_per_min: int = 100

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
