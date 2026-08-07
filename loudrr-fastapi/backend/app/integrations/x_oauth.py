"""X (Twitter) OAuth 2.0 — Authorization Code flow with PKCE (spec §7, Ch11).

Used once to prove a user controls the X handle they claim. We don't keep the
access token; we only read /users/me to confirm ownership. Pure transport +
PKCE helpers here — the state lifecycle (store/consume) lives in the service,
since it needs the DB.

Refs: developer.x.com OAuth 2.0 Authorization Code; RFC 7636 (PKCE).
"""
import base64
import hashlib
import logging
import secrets
from typing import Optional
from urllib.parse import urlencode

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

AUTHORIZE_URL = "https://twitter.com/i/oauth2/authorize"
TOKEN_URL = "https://api.twitter.com/2/oauth2/token"
USERS_ME_URL = "https://api.twitter.com/2/users/me"

# read user info to confirm identity
SCOPES = "users.read tweet.read"
STATE_TTL_SECONDS = 600  # the authorize state is valid for 10 minutes
_TIMEOUT = httpx.Timeout(20.0)


def _b64url_no_pad(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def make_pkce() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) — S256."""
    verifier = _b64url_no_pad(secrets.token_bytes(48))
    challenge = _b64url_no_pad(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def new_state() -> str:
    return secrets.token_urlsafe(32)


def is_configured() -> bool:
    return bool(settings.x_oauth_client_id and settings.x_oauth_callback_url)


def build_authorize_url(state: str, code_challenge: str) -> str:
    """Construct the X authorize URL for a given state + PKCE challenge.

    Raises RuntimeError if OAuth isn't configured (caller maps that to 503).
    """
    if not settings.x_oauth_client_id:
        raise RuntimeError("X_OAUTH_CLIENT_ID not configured")
    params = {
        "response_type": "code",
        "client_id": settings.x_oauth_client_id,
        "redirect_uri": settings.x_oauth_callback_url,
        "scope": SCOPES,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


async def exchange_code_for_token(code: str, code_verifier: str) -> Optional[str]:
    """Exchange an auth code for an access token, or None on failure (logged)."""
    auth_header = base64.b64encode(
        f"{settings.x_oauth_client_id}:{settings.x_oauth_client_secret}".encode()
    ).decode("ascii")
    body = {
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": settings.x_oauth_callback_url,
        "code_verifier": code_verifier,
        "client_id": settings.x_oauth_client_id,
    }
    headers = {
        "Authorization": f"Basic {auth_header}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.post(TOKEN_URL, data=body, headers=headers)
    except httpx.HTTPError:
        logger.exception("[X-OAUTH] token exchange exception")
        return None
    if r.status_code != 200:
        logger.warning("[X-OAUTH] token exchange failed: %s %s", r.status_code, r.text[:300])
        return None
    return r.json().get("access_token")


async def fetch_me(access_token: str) -> Optional[dict]:
    """Fetch the authorized user's profile: {'id', 'username', 'name'} or None."""
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(USERS_ME_URL, headers=headers)
    except httpx.HTTPError:
        logger.exception("[X-OAUTH] users/me exception")
        return None
    if r.status_code != 200:
        logger.warning("[X-OAUTH] users/me failed: %s %s", r.status_code, r.text[:300])
        return None
    return r.json().get("data") or None
