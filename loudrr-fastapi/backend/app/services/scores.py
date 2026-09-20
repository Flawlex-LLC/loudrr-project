"""When scores are fetched — sign-up and the refresh button. No scheduler.

  * sign-up: register_entry queues ``fetch_waitlist_score``, which stores the
    result on the WaitlistEntry. Approval copies it onto the new User.
  * the mini-app "Refresh score" button (POST /user/refresh-score/), at most
    once per SCORE_REFRESH_COOLDOWN_MINUTES per account.

Everything else — the waitlist card, the admin tables, tiers — READS the
stored score and never calls the provider. (Linking a handle and first-run
onboarding still fetch when a user has no score at all; both are user
actions.) The provider call is always made with no DB lock held.
"""
import logging
import uuid
from datetime import timedelta

from app.core.errors import BadRequest, Forbidden
from app.core.time_utils import utcnow
from app.integrations.score_provider import get_score_client
from app.models.user import User
from app.models.waitlist_entry import WaitlistEntry
from app.repositories.user import UserRepository
from app.repositories.waitlist import WaitlistRepository
from app.repositories.x_profile import XProfileRepository
from app.services import tier as tier_svc
from app.services.site_settings import get_setting
from app.services.users import _profile_values, _upsert_x_profile

logger = logging.getLogger(__name__)

_CARD_FOLLOWERS = 10


def _other_account(data: dict | None, oauth_x_user_id: str | None) -> bool:
    """The provider answered for a DIFFERENT X account than the one OAuth
    proved (a renamed/recycled handle still cached under the old owner). Its
    score must never be credited to this user."""
    provider_id = str((data or {}).get("id") or "")
    return bool(provider_id and oauth_x_user_id and provider_id != str(oauth_x_user_id))


def _provider_unavailable(client) -> bool:
    """A miss that is OUR problem (proxies timing out, provider pushing back),
    as opposed to the provider not knowing the account. Never record it as
    "no score" or start the user's refresh cooldown."""
    check = getattr(client, "is_unavailable", None) or getattr(client, "is_blocked", None)
    return bool(check()) if check else False


def _card(score, data, fetched_at) -> dict:
    """The card-facing view of a stored score: WaitlistEnrichmentResponse
    fields minus x_username."""
    data = data if isinstance(data, dict) else {}
    followers: list[str] = []
    for acc in data.get("top_followers") or []:
        name = str(acc.get("username") or "").strip().lstrip("@") if isinstance(acc, dict) else ""
        if name and name not in followers:
            followers.append(name)
        if len(followers) >= _CARD_FOLLOWERS:
            break
    # "Followed by N smart accounts": the provider's total, else what we show
    followers_count = len(followers)
    total = data.get("smart_followers")
    if followers and isinstance(total, int) and total > followers_count:
        followers_count = total
    if score is not None:
        status = "ready"
    elif fetched_at is None:
        status = "pending"      # the sign-up fetch hasn't finished yet
    else:
        status = "not_found"    # fetched, but the provider has no score
    return {
        "score": score,
        "tier": tier_svc.tier_for(score) if score is not None else None,
        "followers": followers,
        "followers_count": followers_count,
        "score_status": status,
        "score_updated_at": fetched_at.isoformat() if fetched_at else None,
    }


async def stored_card(db, *, user: User | None = None, entry: WaitlistEntry | None = None) -> dict:
    """The stored score for an approved user (User + x_profiles) or an
    applicant (WaitlistEntry). Never calls the provider."""
    if user is not None:
        profile = await XProfileRepository(db).get(user_id=user.id)
        data = (profile.raw_tweetscout_data if profile else None) or {}
        scored = user.tweetscout_last_updated is not None and (
            bool(data) or (user.tweetscout_score or 0) > 0
        )
        score = float(user.tweetscout_score or 0) if scored else None
        return _card(score, data, user.tweetscout_last_updated)
    if entry is not None:
        return _card(entry.score, entry.score_data, entry.score_updated_at)
    return _card(None, None, None) | {"score_status": "not_found"}


def _store_on_entry(entry: WaitlistEntry, data: dict | None) -> None:
    """Record a finished fetch. A miss keeps any earlier score (never punitive)."""
    if data:
        entry.score = float(data.get("score", 0) or 0)
        entry.score_data = data
    entry.score_updated_at = utcnow()


# ---- sign-up (arq job queued by register_entry) ----
async def fetch_waitlist_score(db, entry_id) -> str:
    """Fetch a new applicant's score once and store it on their entry.

    Returns "found" | "not_found" | "unavailable" | "skipped". An unavailable
    fetch records nothing — the card stays "pending" and the arq job retries
    (tasks/worker.py) — so our outage never shows as "no score" or puts the
    applicant's Refresh button on cooldown."""
    entry = await db.get(WaitlistEntry, uuid.UUID(str(entry_id)))
    if entry is None or not entry.x_username:
        return "skipped"
    if entry.score_updated_at is not None:
        return "skipped"  # already fetched (a retry raced a Refresh tap)
    handle, oauth_id = entry.x_username, entry.x_user_id
    # end the read transaction first: never hold a pooled DB connection
    # across a provider call that can take seconds (expire_on_commit=False
    # keeps `entry` usable)
    await db.commit()
    client = get_score_client()
    data = await client.get_user_data(handle)
    if data is None and _provider_unavailable(client):
        return "unavailable"
    if _other_account(data, oauth_id):
        logger.warning("score provider returned another X account for @%s — ignoring it", handle)
        data = None
    _store_on_entry(entry, data)
    await db.commit()
    return "found" if data is not None else "not_found"


# ---- approval: carry the sign-up score over (no new fetch) ----
async def seed_user_from_entry(db, *, entry: WaitlistEntry, user: User) -> None:
    """Give a just-approved User the profile + score fetched at sign-up.
    Without a stored score the User starts unscored, and first-run onboarding
    fetches it. The OAuth-proven x_user_id always beats the provider's."""
    values = (
        _profile_values(entry.score_data, entry.x_username)
        if entry.score_data else {"username": entry.x_username}
    )
    if entry.x_user_id:
        values["x_user_id"] = entry.x_user_id
    if entry.score_data or entry.x_user_id:
        await XProfileRepository(db).create(user_id=user.id, **values)
    if entry.score is not None:
        user.tweetscout_score = entry.score
        user.tweetscout_last_updated = entry.score_updated_at or utcnow()


# ---- the mini-app "Refresh score" button ----
async def refresh_score(db, *, telegram_id: int) -> dict:
    """Re-fetch the caller's score now — approved user or applicant alike.

    ``result``: "updated" | "not_found" (provider has no score; the old one is
    kept) | "cooldown" (refreshed recently; see retry_after_seconds) |
    "unavailable" (provider backing off — nothing recorded, try later)."""
    user = await UserRepository(db).get(telegram_id=telegram_id)
    entry = None if user is not None else await WaitlistRepository(db).get(telegram_id=telegram_id)
    owner = user or entry
    handle = ((owner.x_username if owner else "") or "").strip().lstrip("@")
    if not handle:
        raise BadRequest("Link your X account first")
    if (user is not None and user.is_banned) or (entry is not None and entry.status == "rejected"):
        raise Forbidden("Score refresh isn't available for this account")

    async def respond(result: str, retry_after: int = 0) -> dict:
        card = await stored_card(db, user=user, entry=entry)
        return {"x_username": handle, **card, "result": result, "retry_after_seconds": retry_after}

    minutes = int(await get_setting(db, "SCORE_REFRESH_COOLDOWN_MINUTES", 60))
    last = user.tweetscout_last_updated if user is not None else entry.score_updated_at
    if minutes > 0 and last is not None:
        remaining = timedelta(minutes=minutes) - (utcnow() - last)
        if remaining.total_seconds() > 0:
            return await respond("cooldown", int(remaining.total_seconds()) + 1)

    if user is not None:
        profile = await XProfileRepository(db).get(user_id=user.id)
        oauth_id = (profile.x_user_id if profile else "") if user.x_verified else ""
    else:
        oauth_id = entry.x_user_id
    await db.commit()  # release the pooled connection before the provider call
    client = get_score_client()
    data = await client.get_user_data(handle)
    if data is None and _provider_unavailable(client):
        return await respond("unavailable")
    if _other_account(data, oauth_id):
        logger.warning("score provider returned another X account for @%s — ignoring it", handle)
        data = None

    if user is not None:
        if data:
            await _upsert_x_profile(db, user, _profile_values(data, handle))
            user.tweetscout_score = float(data.get("score", 0) or 0)
        user.tweetscout_last_updated = utcnow()
    else:
        _store_on_entry(entry, data)
    await db.commit()
    return await respond("updated" if data else "not_found")
