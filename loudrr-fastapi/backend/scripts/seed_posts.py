"""Seed a few fake posts for local dev so /session/start/ has something to show.

Idempotent: skips posts whose tweet_id already exists. Creates a dedicated
creator User per author handle (NOT the Oxblest debug user — the engage feed
query filters out the requester's own posts, so the dev user has to see posts
from someone else).

Add or remove URLs by editing POSTS below. Each entry needs an x_link; the
tweet_id, author_username are parsed from it.

Run from backend/ with:
    ../.venv/Scripts/python.exe -m scripts.seed_posts
"""
import asyncio
import re
import secrets
from decimal import Decimal

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.post import Post
from app.models.user import User

# Each entry: (x_link, escrow karma to lock).
# Escrow controls the per-engager cost. Keep modest so multiple users can claim.
POSTS: list[tuple[str, int]] = [
    ("https://x.com/adamilenich/status/2061834470278230288", 80),
    ("https://x.com/NaughtalieStone/status/2062542271099375880", 60),
    ("https://x.com/NFCsummit/status/2062481041785311678", 100),
]

# Strip query string + extract (username, tweet_id) from an x.com / twitter.com URL.
_RE = re.compile(r"(?:x\.com|twitter\.com)/([^/]+)/status/(\d+)")


def _parse(url: str) -> tuple[str, str, str]:
    """Return (clean_url, username, tweet_id) or raise ValueError."""
    m = _RE.search(url)
    if not m:
        raise ValueError(f"Bad X URL: {url}")
    username, tweet_id = m.group(1), m.group(2)
    clean = f"https://x.com/{username}/status/{tweet_id}"
    return clean, username, tweet_id


async def _get_or_create_creator(db, *, username: str) -> User:
    """Find or create a fake creator User keyed by x_username. Telegram id is a
    deterministic large negative number so it can't collide with real users."""
    existing = (
        await db.execute(select(User).where(User.x_username == username))
    ).scalar_one_or_none()
    if existing:
        return existing

    # negative ints fit in BigInteger and clearly mark this as a synthetic row
    telegram_id = -abs(hash(username)) % (10**12)
    user = User(
        telegram_id=telegram_id,
        telegram_username=f"fake_{username}",
        x_username=username,
        display_name=username,
        x_verified=True,
        is_whitelisted=True,
        tweetscout_score=100.0,  # arbitrary "Normie tier" score
        referral_code=f"SEED{secrets.token_hex(3).upper()}",
    )
    db.add(user)
    await db.flush()
    return user


async def seed() -> None:
    created_posts, skipped_posts = [], []
    async with SessionLocal() as db:
        for url, karma in POSTS:
            clean_url, username, tweet_id = _parse(url)

            # Idempotency: skip if this tweet_id already has a post
            existing = (
                await db.execute(select(Post).where(Post.tweet_id == tweet_id))
            ).scalar_one_or_none()
            if existing:
                skipped_posts.append(tweet_id)
                continue

            creator = await _get_or_create_creator(db, username=username)
            post = Post(
                user_id=creator.id,
                x_link=clean_url,
                tweet_id=tweet_id,
                tweet_author_username=username,
                tweet_author_name=username,
                tweet_text=f"[seeded] post by @{username}",
                escrow=Decimal(karma),
                initial_escrow=Decimal(karma),
                status="active",
                platform="web",
            )
            db.add(post)
            await db.flush()
            created_posts.append((tweet_id, username, karma, str(post.id)))

        await db.commit()

    print(f"Created posts: {len(created_posts)}")
    for tid, user, karma, pid in created_posts:
        print(f"  + tweet_id={tid}  @{user}  karma={karma}  post_id={pid}")
    if skipped_posts:
        print(f"Skipped (already in DB): {skipped_posts}")
    print()
    print("Test by hitting /session/start/ as Oxblest:")
    print("  curl 'http://localhost:8000/session/start/?telegram_id=6451704338' -X POST -H 'Content-Type: application/json' -d '{}'")
    print("Or open http://localhost:3000/app/engage in the browser.")


if __name__ == "__main__":
    asyncio.run(seed())
