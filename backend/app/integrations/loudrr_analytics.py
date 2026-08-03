"""Loudrr Analytics client — a user's Loudrr Score + X profile from OUR OWN influence graph
(the loudrr-analytics-service), a drop-in replacement for the paid TweetScout client.

Why: the creator app should run on our own data — it stops paying a competitor for scores and it
is the "data backing" that makes creator tiers trustworthy. This mirrors ``TweetScoutClient``'s
interface exactly (``get_user_data(username) -> flat dict with a ``score`` key, or None``) so the
swap in ``services/users.py`` is one line and fully reversible via ``settings.score_source``.

Scale note: the Loudrr Score is 0-6000 (Sorsa-grade), but the tier thresholds top out at 1000
(GOAT) — anyone above 1000 is top tier, which is the intended behavior (most established creators
are top tier; the tiers differentiate the 0-1000 band). So the score is fed straight into
``tier.tier_for`` with NO rescaling.

Failure policy matches TweetScout's: any timeout / non-200 / not-found returns ``None`` (never
raises) so a hiccup in the analytics service degrades to "default score, retry later", never a 500.
The /v1/profile endpoint is public + keyless (the marketing-funnel score), so no API key is needed.
"""
import logging
from typing import Optional

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(15.0)


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
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
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


def get_loudrr_client() -> LoudrrAnalyticsClient:
    """Factory — a client bound to the configured analytics base URL."""
    return LoudrrAnalyticsClient()


def get_score_client():
    """The configured score provider. Default = our own Loudrr graph; set
    ``settings.score_source = "tweetscout"`` to fall back to the legacy paid client instantly
    (both expose the same ``get_user_data(username)`` contract)."""
    if (getattr(settings, "score_source", "loudrr") or "loudrr").lower() == "tweetscout":
        from app.integrations.tweetscout import get_tweetscout_client
        return get_tweetscout_client()
    return get_loudrr_client()
