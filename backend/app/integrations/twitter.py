"""Twitter API client via twitterapi.io (spec §7, Ch13).

Used in Phase 1 of verification to confirm a user replied to a tweet. Like
verification is impossible (X made likes private in 2024), so we only check
replies. This is a *paid, quota-limited* API — every call costs money.

Benefit-of-the-doubt failure policy (spec §0 #8, §5.2): if there's no API key,
or the API errors/times out, verification is treated as **passed + skipped** —
we never punish a user because our verification path is down. Only a definitive
"no reply found" is a real failure.
"""
import logging
import re
from typing import Optional

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

# Default upstream. The loudrr gateway (gateway_base_url + loudrr_gateway_api)
# is a drop-in twitterapi.io-compatible service — same /twitter paths, same
# params, same response shapes, same x-api-key auth (verified 2026-06-18
# against the analytics service which runs against it). When configured, we
# route reply verification through us instead of paying twitterapi.io direct.
_TWITTERAPI_ROOT = "https://api.twitterapi.io"
_TIMEOUT = httpx.Timeout(30.0)


def _resolve_upstream() -> tuple[str, str]:
    """Return (base_url_with_/twitter_suffix, api_key) — prefers the loudrr
    gateway when configured, else falls back to api.twitterapi.io. Read at
    request time (not module import) so a settings change in tests / a
    monkeypatch doesn't need a re-import."""
    if settings.loudrr_gateway_api:
        return (f"{settings.gateway_base_url.rstrip('/')}/twitter", settings.loudrr_gateway_api)
    return (f"{_TWITTERAPI_ROOT}/twitter", settings.twitter_api_key)

_TWEET_ID_PATTERNS = [
    re.compile(r"(?:twitter\.com|x\.com)/\w+/status/(\d+)"),
    re.compile(r"status/(\d+)"),
]


def extract_tweet_id(url: str) -> Optional[str]:
    """Pull the numeric tweet id out of an X/Twitter status URL, or None."""
    if not url:
        return None
    for pattern in _TWEET_ID_PATTERNS:
        m = pattern.search(url)
        if m:
            return m.group(1)
    return None


class TwitterClient:
    def __init__(self, api_key: str | None = None):
        base_url, resolved_key = _resolve_upstream()
        self.base_url = base_url
        # explicit api_key argument (tests / injection) always wins over the
        # resolved default from settings, matching the pre-gateway behavior.
        self.api_key = api_key if api_key is not None else resolved_key

    def _headers(self) -> dict:
        return {"X-API-Key": self.api_key}

    async def verify_reply(
        self, tweet_id: str, x_username: str, *, max_retries: int = 0,
    ) -> dict:
        """Did @x_username reply to tweet_id?

        Returns {passed, reply_verified, like_verified, error, skipped}. A
        missing key / API error / network error returns passed+skipped (benefit
        of the doubt); a missing username is a hard fail.

        ``max_retries`` (live, MAX_VERIFICATION_RETRIES) is the number of
        ADDITIONAL attempts on transient failures only — network errors
        (httpx.HTTPError that is not a status) and 5xx responses. We do NOT
        retry 4xx (they will keep failing) and we do NOT retry a definitive
        ``passed=False`` result (real verification fail, not transient). 0 =
        single attempt (current behavior). Read once at the call site so the
        get_setting hit doesn't happen inside the retry loop.
        """
        if not self.api_key:
            return {"passed": True, "reply_verified": True, "like_verified": True,
                    "error": None, "skipped": True}
        if not x_username:
            return {"passed": False, "reply_verified": False, "like_verified": True,
                    "error": "User has no X username linked", "skipped": False}

        username = x_username.lower().lstrip("@")
        attempts = max(0, int(max_retries)) + 1  # 0 retries => 1 attempt
        last_transient_error: dict | None = None
        for attempt in range(attempts):
            try:
                async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                    resp = await client.get(
                        f"{self.base_url}/tweet/advanced_search",
                        headers=self._headers(),
                        params={"query": f"from:{username} conversation_id:{tweet_id}"},
                    )
                    resp.raise_for_status()
                    data = resp.json()
            except httpx.HTTPStatusError as e:
                status = e.response.status_code
                logger.warning(
                    "Twitter API %s (attempt %s/%s), assuming passed: %s",
                    status, attempt + 1, attempts, e.response.text[:100],
                )
                err = {
                    "passed": True, "reply_verified": True, "like_verified": True,
                    "error": f"API error: {status}", "skipped": True,
                }
                # Only 5xx is transient — 4xx will keep failing, return now.
                if status < 500 or attempt == attempts - 1:
                    return err
                last_transient_error = err
                continue
            except httpx.HTTPError as e:
                logger.warning(
                    "Twitter verification error (attempt %s/%s), assuming passed: %s",
                    attempt + 1, attempts, e,
                )
                err = {
                    "passed": True, "reply_verified": True, "like_verified": True,
                    "error": str(e), "skipped": True,
                }
                if attempt == attempts - 1:
                    return err
                last_transient_error = err
                continue

            # success path — do NOT retry a real passed=False result
            reply_found = len(data.get("tweets", [])) > 0
            return {"passed": reply_found, "reply_verified": reply_found,
                    "like_verified": True, "error": None, "skipped": False}

        # exhausted all attempts on transient errors (defensive — loop returns
        # err on the last attempt above, so we should never get here)
        return last_transient_error or {
            "passed": True, "reply_verified": True, "like_verified": True,
            "error": "max retries exceeded", "skipped": True,
        }

    async def get_tweet_content(self, tweet_id: str) -> Optional[dict]:
        """Fetch a tweet's text, author, media for caching on a Post (Ch14).

        Returns {tweet_id, text, author_id, author_username, author_name,
        author_avatar, media, created_at} or None on error/not-found. The
        author_id is the source of truth for post-ownership validation.
        """
        if not self.api_key:
            logger.warning("No Twitter API key — cannot fetch tweet content")
            return None
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(
                    f"{self.base_url}/tweets", headers=self._headers(),
                    params={"tweet_ids": tweet_id},
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as e:
            logger.error("Twitter get_tweet_content failed for %s: %s", tweet_id, e)
            return None

        tweets = data.get("tweets", [])
        if not tweets:
            return None
        tweet = tweets[0]
        author = tweet.get("author", {})
        media_urls = []
        for media in tweet.get("extendedEntities", {}).get("media", []):
            url = media.get("media_url_https") or media.get("url")
            if url:
                media_urls.append(url)
        return {
            "tweet_id": str(tweet.get("id", tweet_id)),
            "text": tweet.get("text", ""),
            "author_id": str(author.get("id", "")),
            "author_username": author.get("userName", ""),
            "author_name": author.get("name", ""),
            "author_avatar": author.get("profilePicture", ""),
            "media": media_urls,
            "created_at": tweet.get("createdAt", ""),
        }


def get_twitter_client() -> TwitterClient:
    return TwitterClient()
