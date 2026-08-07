"""Posts service — per-user stat blocks (Ch12) + submission & escrow (Ch14).

Submission charges karma into escrow via CreditService.spend; the escrow is
then paid out to engagers (Ch13) and refunded on cancel/expiry.
"""
import logging
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select

from app.core.errors import BadRequest, Conflict, Forbidden, ServiceUnavailable
from app.core.time_utils import utcnow
from app.integrations.twitter import extract_tweet_id, get_twitter_client
from app.models.engagement import Engagement
from app.models.post import Post
from app.models.user import User
from app.repositories.post import PostRepository
from app.repositories.x_profile import XProfileRepository
from app.services.credits import CreditService, InsufficientCreditsError
from app.services.site_settings import get_setting

logger = logging.getLogger(__name__)


async def post_counts(db, user_id) -> dict:
    rows = (
        await db.execute(
            select(Post.status, func.count())
            .where(Post.user_id == user_id)
            .group_by(Post.status)
        )
    ).all()
    by_status = {status: count for status, count in rows}
    return {
        "total": sum(by_status.values()),
        "active": by_status.get("active", 0),
        "completed": by_status.get("completed", 0),
    }


async def engagement_counts(db, user_id) -> dict:
    given = int(
        (
            await db.execute(
                select(func.count())
                .select_from(Engagement)
                .where(Engagement.user_id == user_id)
            )
        ).scalar_one()
    )
    received = int(
        (
            await db.execute(
                select(func.count())
                .select_from(Engagement)
                .join(Post, Engagement.post_id == Post.id)
                .where(Post.user_id == user_id)
            )
        ).scalar_one()
    )
    return {"given": given, "received": received}


async def recent_posts(db, user_id, *, limit: int = 5) -> list[dict]:
    rows = (
        await db.execute(
            select(Post)
            .where(Post.user_id == user_id)
            .order_by(Post.created_at.desc())
            .limit(limit)
        )
    ).scalars().all()
    return [
        {
            "id": str(p.id),
            "x_link": p.x_link,
            "status": p.status,
            "escrow_remaining": float(p.escrow),
            "engagement_progress": p.engagement_progress,
            "created_at": p.created_at.isoformat() if p.created_at else None,
        }
        for p in rows
    ]


def _parse_tweet_dt(value: str):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except (ValueError, TypeError):
        return None  # twitterapi's "Wed Oct 10 ..." format → leave null


# ---- endpoint 14: POST /post/submit/ ----
async def submit_post(db, *, user, x_link: str, karma_amount=None) -> dict:
    if user.is_banned:
        raise Forbidden("Your account has been suspended")

    x_link = (x_link or "").strip()
    if not x_link:
        raise BadRequest("Missing X post link")
    if not any(d in x_link.lower() for d in ("twitter.com", "x.com")):
        raise BadRequest("Invalid link. Please provide a Twitter/X post URL.")

    tweet_id = extract_tweet_id(x_link)
    if not tweet_id:
        raise BadRequest(
            "Could not extract tweet ID from URL. "
            "Please use format: https://x.com/username/status/123456789"
        )

    if not user.x_username:
        raise BadRequest("Please link your X account first before submitting posts.")

    x_profile = await XProfileRepository(db).get(user_id=user.id)
    if x_profile is None or not x_profile.x_user_id:
        raise BadRequest("X account not properly linked. Please re-link your account.")
    stored_user_id = x_profile.x_user_id

    # fetch tweet content — external call, NO DB lock held (spec §7)
    content = await get_twitter_client().get_tweet_content(tweet_id)
    if not content:
        raise ServiceUnavailable("Could not fetch tweet. Please check the URL and try again.")

    # ownership by permanent author id (handles @ renames)
    if content.get("author_id", "") != stored_user_id:
        raise BadRequest(
            f"This post belongs to @{content.get('author_username', 'unknown')}. "
            "You can only submit your own posts."
        )
    new_username = content.get("author_username", "")
    if new_username and new_username.lower() != (user.x_username or "").lower():
        user.x_username = new_username
        x_profile.username = new_username

    post_cost_min = await get_setting(db, "POST_COST_MIN")
    post_cost_max = await get_setting(db, "POST_COST_MAX")
    karma = post_cost_min if karma_amount is None else karma_amount
    try:
        karma = int(karma)
    except (TypeError, ValueError):
        raise BadRequest("Invalid karma amount")
    if karma < post_cost_min:
        raise BadRequest(f"Minimum karma is {post_cost_min}")
    if karma > post_cost_max:
        raise BadRequest(f"Maximum karma is {post_cost_max}")
    if user.credits < karma:
        raise BadRequest(f"Not enough karma. You need {karma} karma to post.")

    posts = PostRepository(db)
    if await posts.exists(x_link=x_link, status="active"):
        raise BadRequest("This post is already active in the system.")

    # Lock the poster's row BEFORE inserting the post. Inserting a post takes a
    # FK share-lock on users; spend() then needs FOR UPDATE — that upgrade
    # deadlocks two concurrent submits by the same user. Taking FOR UPDATE first
    # gives every path the same lock order, so the loser blocks then fails
    # gracefully (insufficient credits) instead of erroring on a deadlock. The
    # external tweet fetch above held no lock (spec §7); none is held until here.
    await db.execute(
        select(User).where(User.id == user.id)
        .with_for_update().execution_options(populate_existing=True)
    )

    post = await posts.create(
        user_id=user.id, x_link=x_link, tweet_id=tweet_id, platform="web",
        escrow=Decimal(str(karma)), initial_escrow=Decimal(str(karma)),
        tweet_text=content.get("text", ""),
        tweet_author_name=content.get("author_name", ""),
        tweet_author_username=content.get("author_username", ""),
        tweet_author_avatar=content.get("author_avatar", ""),
        tweet_media=content.get("media", []),
        tweet_created_at=_parse_tweet_dt(content.get("created_at", "")),
    )
    try:
        # spend locks the user row + re-checks the balance (the real guard
        # against two concurrent submits draining past zero)
        await CreditService(db, user).spend(
            Decimal(str(karma)), idempotency_key=str(post.id),
            reference_id=post.id, description="Posted X link",
        )
    except InsufficientCreditsError:
        await db.rollback()
        raise BadRequest(f"Not enough karma. You need {karma} karma to post.")

    user.total_posts += 1
    await db.commit()

    return {
        "success": True,
        "message": f"Post submitted! {karma} karma locked in escrow.",
        "post_id": str(post.id),
        "new_balance": float(user.credits),
        "escrow": karma,
    }


# ---- escrow lifecycle transitions ----
async def cancel_post(db, post: Post, *, refund: bool = True) -> Post:
    """active → cancelled. Refund the remaining escrow to the poster, then
    zero it (satisfies the post_cancelled_zero_escrow constraint). Used by the
    expiry task (Ch16) and admin."""
    if post.status != "active":
        raise Conflict(f"Post is {post.status!r}, cannot cancel")
    if refund and post.escrow > 0:
        from app.models.user import User

        owner = await db.get(User, post.user_id)
        await CreditService(db, owner).refund(
            post.escrow, idempotency_key=f"refund_{post.id}",
            reference_id=post.id, description="Refund for cancelled post",
            # refund commits; we commit again below after zeroing escrow
        )
    post.escrow = Decimal("0")
    post.status = "cancelled"
    post.completed_at = utcnow()
    await db.commit()
    return post
