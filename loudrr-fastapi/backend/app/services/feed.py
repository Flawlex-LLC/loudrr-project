"""The engagement feed — which posts a user sees, scored and formatted (Ch12).

Ported from the Django feed service. A post is eligible when it is active, not
the viewer's own, not already engaged, and still has enough escrow to pay the
viewer's tiered karma. Eligible posts are ranked by a weighted score
(author tier + freshness + remaining escrow).
"""
from decimal import Decimal

from sqlalchemy import select, func

from app.core.config import settings
from app.core.time_utils import utcnow
from app.models.engagement import Engagement
from app.models.post import Post
from app.models.user import User
from app.models.x_profile import XProfile
from app.services import tier
from app.services.site_settings import get_setting


async def _min_escrow(db, user: User) -> Decimal:
    """The least escrow a post must have to afford this user's tiered karma."""
    base = Decimal(str(await get_setting(db, "CREDIT_PER_ENGAGEMENT", 1)))
    return base * tier.multiplier_for(user.tweetscout_score or 0)


def _eligible_query(user: User, min_escrow: Decimal, exclude_post_ids):
    """Active posts that can afford this user's karma, minus own/engaged/excluded."""
    engaged = select(Engagement.post_id).where(Engagement.user_id == user.id)
    q = (
        select(Post)
        .where(
            Post.status == "active",
            Post.escrow >= min_escrow,
            Post.user_id != user.id,
            Post.id.not_in(engaged),
        )
    )
    if exclude_post_ids:
        q = q.where(Post.id.not_in(exclude_post_ids))
    return q


def calculate_feed_score(post: Post, viewer: User) -> float:
    """feed_score = author_tier·0.5 + freshness·0.3 + escrow_remaining·0.2 (0–1).

    `post._author_ts` is the author's TweetScout score, attached by the feed
    query before scoring (so this stays a pure, synchronous function).
    """
    # author tier band → normalized author score
    author_ts = float(getattr(post, "_author_ts", 0.0))
    if author_ts >= 1000:
        author_score = 1.0
    elif author_ts >= 800:
        author_score = 0.9
    elif author_ts >= 600:
        author_score = 0.8
    elif author_ts >= 400:
        author_score = 0.65
    elif author_ts >= 200:
        author_score = 0.5
    elif author_ts >= 100:
        author_score = 0.35
    else:
        author_score = 0.2

    # freshness: decays to 0 over 7 days (168h)
    hours_old = (utcnow() - post.created_at).total_seconds() / 3600
    freshness = max(0.0, 1.0 - (hours_old / 168))

    # remaining escrow ratio
    initial = float(post.initial_escrow or 1)
    escrow_ratio = float(post.escrow) / initial if initial > 0 else 0.0

    return min((author_score * 0.5) + (freshness * 0.3) + (escrow_ratio * 0.2), 1.0)


async def author_map(db, posts) -> dict:
    """user_id → (User, XProfile|None) for a batch of posts (avoids N+1)."""
    user_ids = {p.user_id for p in posts}
    if not user_ids:
        return {}
    users = (await db.execute(select(User).where(User.id.in_(user_ids)))).scalars().all()
    profiles = (
        await db.execute(select(XProfile).where(XProfile.user_id.in_(user_ids)))
    ).scalars().all()
    prof_by_uid = {pr.user_id: pr for pr in profiles}
    return {u.id: (u, prof_by_uid.get(u.id)) for u in users}


async def get_feed_posts(db, user: User, *, limit: int = 100, exclude_post_ids=None):
    """Eligible posts, scored and sorted (highest first), capped at `limit`."""
    min_escrow = await _min_escrow(db, user)
    rows = (
        await db.execute(_eligible_query(user, min_escrow, exclude_post_ids))
    ).scalars().all()

    # attach author tweetscout for scoring (one batched lookup)
    amap = await author_map(db, rows)
    for p in rows:
        author = amap.get(p.user_id)
        p._author_ts = (author[0].tweetscout_score if author else 0) or 0

    scored = sorted(rows, key=lambda p: calculate_feed_score(p, user), reverse=True)
    return scored[:limit]


async def get_feed_count(db, user: User) -> int:
    """How many posts are eligible for this user to engage with."""
    min_escrow = await _min_escrow(db, user)
    engaged = select(Engagement.post_id).where(Engagement.user_id == user.id)
    q = select(func.count()).select_from(Post).where(
        Post.status == "active",
        Post.escrow >= min_escrow,
        Post.user_id != user.id,
        Post.id.not_in(engaged),
    )
    return int((await db.execute(q)).scalar_one())


async def engaged_today_count(db, user: User) -> int:
    """Engagements this user clicked since midnight UTC."""
    midnight = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    q = select(func.count()).select_from(Engagement).where(
        Engagement.user_id == user.id, Engagement.clicked_at >= midnight
    )
    return int((await db.execute(q)).scalar_one())


def _redirect_url(post: Post) -> str:
    # The encrypted ?u= tracking param + the /r/{token}/ handler are deferred
    # (browser-redirect extra, not one of the 20 contract endpoints).
    base = settings.site_url or ""
    return f"{base}/r/{post.redirect_token}/"


async def format_post(db, post: Post, viewer: User, *, author=None, x_profile=None) -> dict:
    """Build the <post> object the frontend expects (spec §4.1)."""
    if author is None:
        amap = await author_map(db, [post])
        author, x_profile = amap.get(post.user_id, (None, None))

    expiry_hours = await get_setting(db, "POST_EXPIRY_HOURS", 48)
    if post.created_at:
        elapsed_h = (utcnow() - post.created_at).total_seconds() / 3600
        hours_remaining = max(0.0, expiry_hours - elapsed_h)
    else:
        hours_remaining = float(expiry_hours)

    if x_profile:
        display_name = (
            x_profile.display_name
            or (author.x_username if author else None)
            or (author.display_name if author else None)
            or "Anonymous"
        )
        x_username = x_profile.username or (author.x_username if author else None)
        avatar_url = x_profile.avatar_url or None
    else:
        display_name = (
            (author.display_name if author else None)
            or (author.telegram_username if author else None)
            or "Anonymous"
        )
        x_username = author.x_username if author else None
        avatar_url = None

    return {
        "id": str(post.id),
        "x_link": post.x_link,
        "redirect_url": _redirect_url(post),
        "creator": display_name,
        "creator_x_username": x_username,
        "creator_avatar": avatar_url,
        "escrow_remaining": float(post.escrow),
        "engagement_progress": post.engagement_progress,
        "is_sponsored": post.is_sponsored,
        "tweet_id": post.tweet_id or None,
        "created_at": post.created_at.isoformat() if post.created_at else None,
        "tweet_text": post.tweet_text or None,
        "tweet_author_name": post.tweet_author_name or None,
        "tweet_author_username": post.tweet_author_username or None,
        "tweet_author_avatar": post.tweet_author_avatar or None,
        "tweet_media": post.tweet_media or [],
        "tweet_created_at": (
            post.tweet_created_at.isoformat() if post.tweet_created_at else None
        ),
        "hours_remaining": round(hours_remaining, 1),
    }
