"""Loudrr Analytics client — the ONLY score provider.

A user's Loudrr Score + X profile comes from OUR OWN influence graph
(the loudrr-analytics-service). We deliberately do NOT fall back to any
external paid service; if the analytics service is down / unset, the client
returns None and callers degrade gracefully to "default score, retry later"
(never a 500, never a punitive zero for the user).

Scale note: the Loudrr Score is 0-6000, but the tier thresholds top out at
1000 (GOAT) — anyone above 1000 is top tier, which is the intended behavior
(most established creators are top tier; the tiers differentiate the 0-1000
band). So the score is fed straight into ``tier.tier_for`` with NO rescaling.

Failure policy: any timeout / non-200 / not-found returns ``None`` (never
raises). The /v1/profile endpoint is currently keyless (marketing-funnel);
the miniapp-facing endpoints (/v1/score, /v1/top-followers, /v1/score-changes,
/v1/followers-stats, /v1/top-following) are gated by X-API-Key when
``settings.loudrr_analytics_key`` is set — we send the header on EVERY
request (harmless if the endpoint doesn't check it, required when it does).
"""
import logging
from typing import Optional

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(15.0)


def _auth_headers() -> dict[str, str]:
    """Build the auth header dict. Empty when no key is configured so httpx
    just sends no X-API-Key (backwards-compat with the keyless historical
    behavior; also lets local dev hit /v1/profile without any config)."""
    key = settings.loudrr_analytics_key
    return {"X-API-Key": key} if key else {}


class LoudrrAnalyticsClient:
    def __init__(self, base_url: str | None = None):
        self.base_url = (base_url if base_url is not None else settings.loudrr_analytics_url).rstrip("/")

    @staticmethod
    def _clean(username: str) -> str:
        return username.strip().lstrip("@")

    async def get_user_data(self, username: str) -> Optional[dict]:
        """Combined Loudrr Score + X profile as one flat dict (TweetScout-compatible shape), or
        None if the account can't be resolved/scored. Keys mirror what ``_profile_values`` reads."""
        if not self.base_url:
            logger.warning("LOUDRR_ANALYTICS_URL not configured")
            return None
        u = self._clean(username)
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT, headers=_auth_headers()) as client:
                resp = await client.get(f"{self.base_url}/v1/profile", params={"userName": u})
        except httpx.HTTPError as e:
            logger.error("Loudrr analytics request failed for %s: %s", u, e)
            return None
        if resp.status_code != 200:
            logger.error("Loudrr analytics error %s for %s", resp.status_code, u)
            return None
        d = resp.json() or {}
        if not d.get("found"):
            logger.warning("Loudrr analytics: %s not found/resolvable", u)
            return None
        # Flatten to the TweetScout payload shape the caller (_profile_values) already understands.
        return {
            "id": d.get("userId"),
            "screen_name": d.get("userName") or u,
            "name": d.get("name"),
            "description": d.get("bio"),
            "followers_count": d.get("followers"),
            "friends_count": d.get("following"),
            "tweets_count": 0,                      # not exposed by /v1/profile
            "score": float(d.get("score") or 0),   # the Loudrr Score (0-6000), fed straight to tiers
            "avatar": d.get("image"),
            "banner": "",
            "verified": bool(d.get("verified", False)),
            "can_dm": False,
            "register_date": None,
            # Loudrr-native extras (harmless to the TweetScout-shaped caller; useful for receipts):
            "loudrr_rank": d.get("rank"),
            "loudrr_percentile": d.get("percentile"),
            "smart_followers": d.get("eliteFollowers"),
        }

    async def get_top_followers(self, username: str, k: int = 10) -> Optional[list[dict]]:
        """Top-N smart-set members who follow ``username``, ranked by Loudrr Score.

        Returns a list of user dicts (each with userName, name, score, ...) or
        None on any error. ``k`` defaults to 10 for the miniapp's "Top 10
        Smart Followers" panel; bump higher (up to 100) for deeper views.

        The endpoint is GATED — needs settings.loudrr_analytics_key to be set
        matching the analytics service's ANALYTICS_API_KEY, or the analytics
        service responds 401.
        """
        if not self.base_url:
            logger.warning("LOUDRR_ANALYTICS_URL not configured")
            return None
        u = self._clean(username)
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT, headers=_auth_headers()) as client:
                resp = await client.get(
                    f"{self.base_url}/v1/top-followers",
                    params={"userName": u, "k": k},
                )
        except httpx.HTTPError as e:
            logger.error("top-followers request failed for %s: %s", u, e)
            return None
        if resp.status_code == 401:
            logger.error("top-followers 401 for %s — LOUDRR_ANALYTICS_KEY missing/wrong", u)
            return None
        if resp.status_code != 200:
            logger.error("top-followers error %s for %s", resp.status_code, u)
            return None
        return (resp.json() or {}).get("users") or []


def get_loudrr_client() -> LoudrrAnalyticsClient:
    """Factory — a client bound to the configured analytics base URL."""
    return LoudrrAnalyticsClient()


def get_score_client():
    """The score provider — always the Loudrr analytics graph. Kept as a
    separate factory (rather than inlining LoudrrAnalyticsClient at call
    sites) so tests can monkeypatch this ONE symbol to inject a fake
    provider without having to reach into the analytics module internals."""
    return get_loudrr_client()
