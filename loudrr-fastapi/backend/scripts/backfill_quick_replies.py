"""Write Quick Reply drafts for active posts that don't have them yet.

New posts get drafts from the worker as they're created; this covers the posts
that were already live when Quick Reply shipped. Sponsored posts go first.
--redo rewrites the drafts of every active post (after the rules change).

    python -m scripts.backfill_quick_replies                   # dry run: how many need drafts
    python -m scripts.backfill_quick_replies --apply           # write them, one post at a time
    python -m scripts.backfill_quick_replies --redo --apply    # rewrite all active posts' drafts
"""
import argparse
import asyncio

from sqlalchemy import func, select, update

from app.core.time_utils import utcnow
from app.db.session import SessionLocal
from app.models.post import Post
from app.services import quick_replies


async def _rewrite(db, post_id) -> str:
    """New drafts replace the old ones only once they exist, so a failed model
    call never leaves a live post without drafts."""
    post = await db.get(Post, post_id)
    await db.commit()  # don't hold a connection across the model call
    drafts, model = await quick_replies.write_drafts(post)
    if not drafts:
        return "failed (kept the old drafts)"
    await db.execute(update(Post).where(Post.id == post_id).values(
        quick_replies=drafts, quick_replies_model=model, quick_replies_at=utcnow()))
    await db.commit()
    return "done"


async def main(apply: bool, redo: bool) -> None:
    async with SessionLocal() as db:
        query = select(Post.id).where(Post.status == "active")
        if not redo:
            query = query.where(func.jsonb_array_length(Post.quick_replies) == 0)
        ids = (await db.execute(query.order_by(Post.is_sponsored.desc(), Post.created_at))).scalars().all()
    print(f"{len(ids)} active posts {'to rewrite' if redo else 'without drafts'}")
    if not apply:
        return
    done = 0
    for post_id in ids:
        async with SessionLocal() as db:
            if redo:
                outcome = await _rewrite(db, post_id)
            else:
                outcome = await quick_replies.generate_for_post(db, post_id)
        done += outcome == "done"
        print(f"  {post_id}: {outcome}")
    print(f"drafts written for {done} of {len(ids)} posts")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--redo", action="store_true")
    a = ap.parse_args()
    asyncio.run(main(a.apply, a.redo))
