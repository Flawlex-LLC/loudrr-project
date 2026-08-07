"""Phase 1 of verification — external Twitter calls, NO database (spec §5.2).

This phase makes the slow, fail-prone HTTP calls while holding no lock, and
produces a plain list of pass/fail results. Phase 2 (settlement) then writes
the money atomically. Skipped (no tweet_id / no API key / API down) counts as
**passed** — benefit of the doubt.
"""
import logging
import random
import uuid
from dataclasses import dataclass

from app.integrations import twitter
from app.services.site_settings import get_setting

logger = logging.getLogger(__name__)


@dataclass
class ToVerify:
    engagement_id: uuid.UUID
    post_id: uuid.UUID
    tweet_id: str


@dataclass
class VResult:
    engagement_id: uuid.UUID
    post_id: uuid.UUID
    passed: bool
    reply_verified: bool = False
    like_verified: bool = True  # always true — X made likes private
    skipped: bool = False
    error: str | None = None


async def _verify_single(
    client, item: ToVerify, x_username: str, *, max_retries: int = 0,
) -> VResult:
    if not item.tweet_id:
        return VResult(item.engagement_id, item.post_id, passed=True, skipped=True,
                       error="No tweet_id available")  # benefit of the doubt
    if not x_username:
        return VResult(item.engagement_id, item.post_id, passed=False,
                       error="User has no X account linked")

    api = await client.verify_reply(
        tweet_id=item.tweet_id, x_username=x_username, max_retries=max_retries,
    )
    if api.get("skipped"):
        return VResult(item.engagement_id, item.post_id, passed=True,
                       reply_verified=False, skipped=True, error=api.get("error"))
    passed = api.get("passed", False)
    return VResult(
        item.engagement_id, item.post_id, passed=passed,
        reply_verified=api.get("reply_verified", False), like_verified=True,
        error=None if passed else api.get("error"),
    )


async def verify_engagements(
    items: list[ToVerify], x_username: str, *, db=None,
) -> list[VResult]:
    """Run Phase 1 over a batch. No DB writes, no locks — only HTTP.

    Three live settings compose here:

    * ``AUDIT_PROBABILITY`` (default 1.0) — per-item Bernoulli gate. A roll
      above the threshold short-circuits to a trusted pass — preserves API
      budget while still randomly sampling for fraud. 1.0 = verify every item.
    * ``VERIFICATION_SAMPLE_SIZE`` (default 0 = no cap) — batch-level CAP on
      how many items in this batch get the real API audit. Composes with
      AUDIT_PROBABILITY: an item is audited only if the probability roll
      passes AND the per-batch audit budget hasn't been spent. Once the cap
      is hit, the remaining items get a trusted-pass even if their roll said
      "audit". 0 (and any value >= len(items)) means "no cap" — preserves
      pure AUDIT_PROBABILITY behavior.
    * ``MAX_VERIFICATION_RETRIES`` (default 0) — passed down to the Twitter
      client as the number of ADDITIONAL attempts on transient failures
      (network errors / 5xx). Read once here so the get_setting hit doesn't
      happen inside the retry loop.

    Caller passes ``db`` so we can read the settings; if ``db`` is None we
    keep the legacy "verify everything, no retries" path so existing tests
    and unit calls don't regress."""
    client = twitter.get_twitter_client()
    audit_prob = 1.0
    sample_size = 0
    max_retries = 0
    if db is not None:
        # cast defensively — settings can come back as Decimal/float/str.
        audit_prob = float(await get_setting(db, "AUDIT_PROBABILITY", 1.0))
        sample_size = int(await get_setting(db, "VERIFICATION_SAMPLE_SIZE", 0))
        max_retries = int(await get_setting(db, "MAX_VERIFICATION_RETRIES", 0))

    # sample_size <= 0 OR sample_size >= len(items) is the "no cap" sentinel.
    no_cap = sample_size <= 0 or sample_size >= len(items)
    audits_remaining = len(items) if no_cap else sample_size

    results: list[VResult] = []
    for it in items:
        roll_says_audit = audit_prob >= 1.0 or random.random() < audit_prob
        if not roll_says_audit or audits_remaining <= 0:
            # trusted skip — counts as passed, no API call. Distinct from the
            # "tweet_id missing / API skipped" path: error stays None.
            results.append(VResult(
                it.engagement_id, it.post_id, passed=True,
                reply_verified=False, skipped=True, error=None,
            ))
            continue
        audits_remaining -= 1
        results.append(await _verify_single(
            client, it, x_username, max_retries=max_retries,
        ))
    passed = sum(1 for r in results if r.passed)
    logger.info("Phase 1 done: %s passed, %s failed", passed, len(results) - passed)
    return results
