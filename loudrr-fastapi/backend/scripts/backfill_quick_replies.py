"""Write Quick Reply drafts for active posts that don't have them yet.

New posts get drafts from the worker as they're created; this covers the posts
that were already live when Quick Reply shipped. Sponsored posts go first.

    python -m scripts.backfill_quick_replies            # dry run: how many need drafts
    python -m scripts.backfill_quick_replies --apply    # write them, one post at a time
"""
import argparse
import asyncio

from sqlalchemy import func, select

from app.db.session import SessionLocal
from app.models.post import Post
from app.services import quick_replies


async def main(apply: bool) -> None:
    async with SessionLocal() as db:
        ids = (await db.execute(
            select(Post.id)
            .where(Post.status == "active", func.jsonb_array_length(Post.quick_replies) == 0)
            .order_by(Post.is_sponsored.desc(), Post.created_at)
        )).scalars().all()
    print(f"{len(ids)} active posts without drafts")
    if not apply:
        return
    done = 0
    for post_id in ids:
        async with SessionLocal() as db:
            outcome = await quick_replies.generate_for_post(db, post_id)
        done += outcome == "done"
        print(f"  {post_id}: {outcome}")
    print(f"drafts written for {done} of {len(ids)} posts")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    asyncio.run(main(ap.parse_args().apply))
