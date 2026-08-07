"""The engagement session (Ch12) — endpoints 9, 10, 11.

A "session" is not a stored object; a user's progress IS their set of pending
(unverified, uncredited) engagements, which persists across visits. Clicking a
post creates one engagement; credit is awarded later by the Ch13 claim engine.
"""

from sqlalchemy import func, select

from app.core.db_helpers import locked_row
from app.core.errors import BadRequest, Forbidden, NotFound
from app.core.time_utils import utcnow
from app.models.engagement import Engagement
from app.models.post import Post
from app.repositories.engagement import EngagementRepository
from app.services import feed
from app.services.site_settings import get_setting


async def _pending_count(db, user_id) -> int:
    q = select(func.count()).select_from(Engagement).where(
        Engagement.user_id == user_id,
        Engagement.verified.is_(False),
        Engagement.credit_granted.is_(False),
    )
    return int((await db.execute(q)).scalar_one())


# ---- endpoint 9: POST /session/start/ ----
async def start_session(db, *, user) -> dict:
    if user.is_banned:
        raise Forbidden("Your account has been suspended")

    # the user's progress = their pending engagements, oldest first (FIFO)
    pending = (
        await db.execute(
            select(Engagement)
            .where(
                Engagement.user_id == user.id,
                Engagement.verified.is_(False),
                Engagement.credit_granted.is_(False),
            )
            .order_by(Engagement.clicked_at)
        )
    ).scalars().all()
    pending_count = len(pending)
    pending_post_ids = [e.post_id for e in pending]

    # the posts behind those pending engagements (still active + funded)
    pending_posts = []
    if pending_post_ids:
        rows = (
            await db.execute(select(Post).where(Post.id.in_(pending_post_ids)))
        ).scalars().all()
        by_id = {p.id: p for p in rows}
        for pid in pending_post_ids:  # preserve FIFO order
            p = by_id.get(pid)
            if p and p.status == "active" and p.escrow > 0:
                pending_posts.append(p)

    fresh = await feed.get_feed_posts(
        db, user, limit=100, exclude_post_ids=pending_post_ids
    )
    all_posts = pending_posts + fresh

    daily_cap = await get_setting(db, "DAILY_EARN_CAP")
    user_block = {
        "credits": float(user.credits),
        "daily_earned": float(user.daily_credits_earned),
        "daily_cap": daily_cap,
    }

    if not all_posts and pending_count == 0:
        return {
            "posts": [],
            "pending_count": 0,
            "pending_post_ids": [],
            "show_verification": False,
            "message": "No posts available right now. Check back later!",
            "user": user_block,
        }

    amap = await feed.author_map(db, all_posts)
    posts_data = [
        await feed.format_post(
            db, p, user, author=amap.get(p.user_id, (None, None))[0],
            x_profile=amap.get(p.user_id, (None, None))[1],
        )
        for p in all_posts
    ]

    return {
        "posts": posts_data,
        "pending_count": pending_count,
        "pending_post_ids": [str(pid) for pid in pending_post_ids],
        "show_verification": False,
        "user": user_block,
    }


# ---- endpoint 10: POST /session/click/ ----
async def record_click(db, *, user, post_id) -> dict:
    if user.is_banned:
        raise Forbidden("Your account has been suspended")
    if not post_id:
        raise BadRequest("Missing post_id")

    # ENGAGEMENT_COOLDOWN — admin-tunable lockout (seconds) between clicks by
    # the same user (parity intent with Django core/services/engagements.py:116).
    # Default 0 = disabled (current behavior; only the unique (user,post) index
    # blocks the dup-click race). Flipped live=True so settings.engagement_cooldown
    # at 5/30/60s starts applying immediately without a deploy.
    cooldown = int(await get_setting(db, "ENGAGEMENT_COOLDOWN", 0))
    if cooldown > 0:
        latest = (
            await db.execute(
                select(Engagement.clicked_at)
                .where(Engagement.user_id == user.id)
                .order_by(Engagement.clicked_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if latest is not None:
            elapsed = (utcnow() - latest).total_seconds()
            if elapsed < cooldown:
                remaining = int(cooldown - elapsed)
                raise BadRequest(
                    f"Please wait {remaining} seconds before engaging again."
                )

    # lock the post row: serializes concurrent clicks, so the check-then-create
    # below is race-free (the unique (user,post) index is the final backstop)
    async with locked_row(db, Post, id=post_id) as post:
        if post.status != "active":
            raise NotFound("Post not found or no longer active")
        if post.user_id == user.id:
            raise BadRequest("Cannot engage with your own post")

        repo = EngagementRepository(db)
        engagement = await repo.get(user_id=user.id, post_id=post.id)
        created = False
        if engagement is None:
            engagement = await repo.create(
                user_id=user.id, post_id=post.id,
                verified=False, credit_granted=False, clicked_at=utcnow(),
            )
            created = True
        eng_id = engagement.id

    pending_count = await _pending_count(db, user.id)
    threshold = await get_setting(db, "MIN_ENGAGEMENTS_TO_CLAIM", 10)
    await db.commit()
    return {
        "success": True,
        "engagement_id": str(eng_id),
        "created": created,
        "pending_count": pending_count,
        "show_verification": pending_count >= threshold,
    }


# ---- endpoint 11: POST /session/verify-return/ ----
async def verify_return(db, *, user, post_id) -> dict:
    if not post_id:
        raise BadRequest("Missing post_id")
    engagement = await EngagementRepository(db).get(user_id=user.id, post_id=post_id)
    if engagement is None:
        raise NotFound("Engagement not found")
    # the engagement existing means the user clicked through and came back
    return {"success": True, "verified": True, "engagement_id": str(engagement.id)}
