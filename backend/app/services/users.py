"""User-facing read models and the TweetScout-backed write flows (Ch10).

Endpoints 2, 3, 4, 8. The two write flows (link-X, onboarding) follow the
golden rule: the TweetScout call happens with **no DB lock held**, then the
result is written. TweetScout is the only paid call here, and its result is
cached in `x_profiles` so it is fetched once, not on every read.
"""
import logging
import re
from datetime import datetime

from app.core.errors import BadRequest
from app.core.time_utils import utcnow
from app.integrations.loudrr_analytics import get_score_client
from app.models.user import User
from app.repositories.x_profile import XProfileRepository
from app.services import feed
from app.services import posts as posts_svc
from app.services import streaks
from app.services import tier
from app.services import x_verification
from app.services.site_settings import get_setting

logger = logging.getLogger(__name__)

_X_USERNAME_RE = re.compile(r"^[a-zA-Z0-9_]{1,15}$")


def _parse_register_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _profile_values(data: dict, fallback_username: str) -> dict:
    """Map a flat TweetScout payload onto XProfile column values."""
    score = float(data.get("score", 0) or 0)
    return {
        "x_user_id": str(data.get("id", "") or ""),
        "username": data.get("screen_name") or fallback_username,
        "display_name": data.get("name", "") or "",
        "bio": data.get("description", "") or "",
        "followers_count": data.get("followers_count", 0) or 0,
        "following_count": data.get("friends_count", 0) or 0,
        "tweets_count": data.get("tweets_count", 0) or 0,
        "score": score,
        "avatar_url": data.get("avatar", "") or "",
        "banner_url": data.get("banner", "") or "",
        "is_verified": bool(data.get("verified", False)),
        "can_dm": bool(data.get("can_dm", False)),
        "x_created_at": _parse_register_date(data.get("register_date")),
        "raw_tweetscout_data": data,
    }


async def _upsert_x_profile(db, user: User, values: dict):
    """Create the user's XProfile, or update it in place if it exists."""
    repo = XProfileRepository(db)
    profile = await repo.get(user_id=user.id)
    if profile is None:
        return await repo.create(user_id=user.id, **values)
    for key, val in values.items():
        setattr(profile, key, val)
    profile.updated_at = utcnow()
    return profile


# ---- endpoint 4: POST /user/link-x/ ----
async def link_x_account(db, *, user: User, x_username: str) -> dict:
    x_username = (x_username or "").strip().lstrip("@")
    if not x_username:
        raise BadRequest("Username is required")
    if not _X_USERNAME_RE.match(x_username):
        raise BadRequest("Invalid username format")

    # external call FIRST, holding no lock (spec §7)
    data = await get_score_client().get_user_data(x_username)
    if data is None:
        raise BadRequest("Username not found. Please check and try again.")

    score = float(data.get("score", 0) or 0)
    profile = await _upsert_x_profile(db, user, _profile_values(data, x_username))

    user.x_username = x_username
    user.tweetscout_score = score
    user.tweetscout_last_updated = utcnow()
    await db.commit()

    return {
        "success": True,
        "x_username": profile.username,
        "tweetscout_score": score,
        "tier": tier.tier_for(score),
        "followers_count": profile.followers_count,
        "display_name": profile.display_name,
    }


# ---- endpoint 8: POST /onboarding/complete/ ----
async def complete_onboarding(db, *, user: User) -> dict:
    if not user.x_username:
        raise BadRequest("X account not linked")

    # already onboarded — a non-zero score means we've already fetched
    if user.tweetscout_score and user.tweetscout_score > 0:
        return {
            "success": True,
            "already_onboarded": True,
            "tweetscout_score": user.tweetscout_score,
            "tier": tier.tier_for(user.tweetscout_score),
        }

    data = await get_score_client().get_user_data(user.x_username)
    if not data:
        # benefit of the doubt: let them in with a default score, retry later
        user.tweetscout_score = 0
        user.tweetscout_last_updated = utcnow()
        await db.commit()
        return {
            "success": True,
            "tweetscout_score": 0,
            "tier": tier.tier_for(0),
            "message": "Could not fetch X data. You can try again later.",
        }

    score = float(data.get("score", 0) or 0)
    await _upsert_x_profile(db, user, _profile_values(data, user.x_username))
    user.tweetscout_score = score
    user.tweetscout_last_updated = utcnow()
    await db.commit()

    return {
        "success": True,
        "tweetscout_score": score,
        "tier": tier.tier_for(score),
        "followers_count": data.get("followers_count", 0) or 0,
        "display_name": data.get("name", "") or "",
    }


# ---- background task (Ch16): refresh a user's TweetScout cache ----
async def fetch_tweetscout_for_user(db, user_id) -> bool:
    """Fetch TweetScout for a user and cache it (on approval / link-X / cron)."""
    user = await db.get(User, user_id)
    if user is None or not user.x_username:
        return False
    data = await get_score_client().get_user_data(user.x_username)
    if not data:
        return False
    score = float(data.get("score", 0) or 0)
    await _upsert_x_profile(db, user, _profile_values(data, user.x_username))
    user.tweetscout_score = score
    user.tweetscout_last_updated = utcnow()
    await db.commit()
    return True


def _next_streak_threshold(current: int) -> int | None:
    """Next streak milestone the user can still hit (or None if past 30)."""
    for t in streaks.THRESHOLDS:
        if current < t:
            return t
    return None


# ---- endpoint 2: GET /user/ ----
async def build_user_info(db, *, user: User) -> dict:
    daily_cap = await get_setting(db, "DAILY_EARN_CAP")
    pending_review = await x_verification.has_pending_review(db, user_id=user.id)
    streak_multiplier = await streaks.get_band_multiplier(db, user.current_streak)
    return {
        "id": str(user.id),
        "display_name": user.display_name,
        "telegram_username": user.telegram_username,
        "x_username": user.x_username or None,
        "credits": float(user.credits),
        "daily_earned": float(user.daily_credits_earned),
        "daily_cap": daily_cap,
        "total_engagements": user.total_engagements,
        "tier": tier.tier_for(user.tweetscout_score),
        "current_streak": user.current_streak,
        "longest_streak": user.longest_streak,
        "streak_multiplier": float(streak_multiplier),
        "streak_next_milestone": _next_streak_threshold(user.current_streak),
        "tweetscout_score": user.tweetscout_score or 0,
        "tweetscout_last_updated": (
            user.tweetscout_last_updated.isoformat()
            if user.tweetscout_last_updated
            else None
        ),
        "honesty_score": user.honesty_score,
        "available_posts": await feed.get_feed_count(db, user),
        "engaged_today": await feed.engaged_today_count(db, user),
        "is_whitelisted": user.is_whitelisted,
        "loud_access": user.loud_access,
        "x_verified": user.x_verified,
        "pending_claimed_x_username": user.pending_claimed_x_username or None,
        "x_verification_pending_review": pending_review,
    }


# ---- endpoint 3: GET /user/stats/ ----
async def build_user_stats(db, *, user: User) -> dict:
    streak_multiplier = await streaks.get_band_multiplier(db, user.current_streak)
    return {
        "user": {
            "display_name": user.display_name,
            "telegram_username": user.telegram_username,
            "credits": float(user.credits),
            "tier": tier.tier_for(user.tweetscout_score),
            "current_streak": user.current_streak,
            "longest_streak": user.longest_streak,
            "streak_multiplier": float(streak_multiplier),
            "streak_next_milestone": _next_streak_threshold(user.current_streak),
            "total_credits_earned": float(user.total_credits_earned),
            "total_credits_spent": float(user.total_credits_spent),
        },
        "posts": await posts_svc.post_counts(db, user.id),
        "engagements": await posts_svc.engagement_counts(db, user.id),
        "recent_posts": await posts_svc.recent_posts(db, user.id),
    }
