"""Resolve a request target (user_link | username | user_id) to a user_id.

Mirrors Sorsa's three-way param. Resolution order is cheap-first: a user_id is used
as-is; a username is looked up in smart_set (free) before falling back to a paid
twitterapi.io /user/info call (cached). This keeps Case-1 queries (already-known
crypto accounts, ~95% of traffic) at zero marginal API cost.
"""
from __future__ import annotations

from sqlalchemy import func, select

from app.clients.twitterapi import TwitterAPIClient
from app.core.util import username_from_link
from app.db.models import SmartSetMember
from app.db.session import SessionLocal


async def resolve_user_id(
    *, user_id: str | None = None, username: str | None = None, user_link: str | None = None
) -> tuple[str | None, dict]:
    """Return (user_id, profile_card). profile_card is {} unless we had to fetch it."""
    if user_id:
        return user_id, {}

    handle = (username or username_from_link(user_link or "") or "").lstrip("@")
    if not handle:
        return None, {}

    async with SessionLocal() as session:
        existing = (
            await session.execute(
                select(SmartSetMember).where(func.lower(SmartSetMember.username) == handle.lower())
            )
        ).scalar_one_or_none()
    if existing:
        return existing.user_id, {
            "username": existing.username,
            "display_name": existing.display_name,
        }

    # Case 2: not a known member -> one cheap, cacheable profile read.
    profile = await TwitterAPIClient().get_user_info(handle)
    if not profile:
        return None, {}
    return str(profile["id"]), {
        "username": profile.get("userName"),
        "display_name": profile.get("name"),
        "followers_count": profile.get("followers"),
        "avatar": profile.get("profilePicture"),
    }
