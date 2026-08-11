"""API-key auth for the analytics HTTP surface.

Behavior:
- If ``settings.analytics_api_key`` is unset (empty), we're in KEYLESS mode
  (the historical default). Endpoints wrapped with ``require_api_key``
  pass through — nothing breaks for old callers. This is how the service
  ships in dev, and how it behaved before this file existed.
- If ``settings.analytics_api_key`` is set to a value, wrapped endpoints
  REQUIRE the request header ``X-API-Key`` to match exactly. Missing /
  wrong header returns 401.

Why opt-in (env-driven) instead of always-on:
- Enables a zero-downtime rollout: deploy this code (keyless), then
  independently set the env var to flip enforcement on. Rollback is
  clearing the env var, no code push needed.
- Keeps dev + CI trivially callable (no key faff for local /health checks).

Comparison against a plaintext env value (not a hash):
- The key IS the secret. Storing a hash of it in code buys nothing — the
  attacker who has read access to memory / env has both anyway.
- Using ``secrets.compare_digest`` gives us constant-time compare so an
  attacker can't infer the key length or a prefix from timing.

Applying to routes: use ``Depends(require_api_key)`` on the specific routes
you want gated (per-route control, not a global middleware — keeps
/health and public marketing endpoints callable without a key).
"""
import secrets

from fastapi import Header, HTTPException, status

from app.core.config import settings


async def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """FastAPI dependency: 401s if a key is configured and the header is
    missing or wrong. No-op when the key is unset (keyless mode)."""
    configured = settings.analytics_api_key
    if not configured:
        # keyless mode — historical behavior, safe for dev + smooth rollout
        return
    if x_api_key is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing X-API-Key header",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    # constant-time compare so an attacker cannot brute-force via timing
    # (Python 'a == b' short-circuits on the first mismatched char).
    if not secrets.compare_digest(x_api_key, configured):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid X-API-Key",
            headers={"WWW-Authenticate": "ApiKey"},
        )
