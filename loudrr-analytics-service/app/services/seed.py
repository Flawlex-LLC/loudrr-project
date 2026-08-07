"""Build the seed of the smart set `M` (research §9).

You do NOT hand-type 50k handles. You import ~1-2k *anchors* and let the follow
graph + PageRank expand `M` to 50k+ (that expansion lives in app.services.crawl /
score). This module handles the import + categorization of the seed.

Primary seed source: **CoinGecko** — ~15k token projects with their X handle, free
and pre-labeled as PROJECT. Secondary: arbitrary handle lists (curated X Lists of
VCs/founders/KOLs) fed in via ``add_handles``. Categorization of non-project seeds
is a cheap bio/name heuristic now; label-propagation over the graph refines it later.
"""
from __future__ import annotations

import asyncio
import csv
import logging

import httpx
from sqlalchemy import case, func

from app.clients.twitterapi import TwitterAPIClient
from app.core.config import settings
from app.core.util import username_from_link
from app.db.models import Category, SmartSetMember
from app.db.session import SessionLocal

logger = logging.getLogger(__name__)

# Dialect-portable upsert (both sqlite/pg expose .on_conflict_do_update + .excluded).
if settings.database_url.startswith("sqlite"):
    from sqlalchemy.dialects.sqlite import insert as _dialect_insert
else:  # pragma: no cover - exercised under Postgres
    from sqlalchemy.dialects.postgresql import insert as _dialect_insert

# SmartSetMember upserts bind 9 cols/row -> ≤100 rows/statement keeps us under
# SQLite's 999 bind-var cap (same convention as calibration._upsert_harvested).
_MEMBER_CHUNK = 100

COINGECKO = "https://api.coingecko.com/api/v3"

# Cheap, transparent bio/name heuristic. Label propagation over the graph (followed
# mostly by VC seeds -> VC-adjacent) refines this in the score job later.
_CATEGORY_KEYWORDS: list[tuple[Category, tuple[str, ...]]] = [
    (Category.VC, ("ventures", "capital", "vc", " gp ", "general partner", "fund", "investing in")),
    (Category.EXCHANGE, ("exchange", "trade crypto", "spot & futures", "cex", "dex aggregator")),
    (Category.MEDIA, ("news", "media", "magazine", "podcast", "newsletter", "coverage")),
    (Category.AUDITOR, ("audit", "security", "smart contract audit", "pentest")),
    (Category.ANGEL, ("angel investor", "angel investing")),
    (Category.FOUNDER, ("founder", "co-founder", "cofounder", "ceo @", "building ")),
    (Category.INFLUENCER, ("kol", "alpha", "trader", "analyst", "calls", "degen")),
]


def categorize(name: str | None, bio: str | None) -> Category:
    text = f"{name or ''} {bio or ''}".lower()
    for category, kws in _CATEGORY_KEYWORDS:
        if any(kw in text for kw in kws):
            return category
    return Category.UNKNOWN


async def _upsert_members(rows: list[dict]) -> int:
    """Idempotent upsert into smart_set keyed on user_id, in bind-safe chunks."""
    if not rows:
        return 0
    async with SessionLocal() as session:
        for i in range(0, len(rows), _MEMBER_CHUNK):
            stmt = _dialect_insert(SmartSetMember).values(rows[i : i + _MEMBER_CHUNK])
            ex = stmt.excluded
            stmt = stmt.on_conflict_do_update(
                index_elements=[SmartSetMember.user_id],
                set_={
                    # never null out good data on a re-seed; keep the existing value
                    # when the incoming fetch is missing it.
                    "username": func.coalesce(ex.username, SmartSetMember.username),
                    "display_name": func.coalesce(ex.display_name, SmartSetMember.display_name),
                    # a re-seed must NOT downgrade a specific category back to 'unknown'.
                    "category": case(
                        (ex.category != Category.UNKNOWN.value, ex.category),
                        else_=SmartSetMember.category,
                    ),
                    "following_count": func.coalesce(ex.following_count, SmartSetMember.following_count),
                    "followers_count": func.coalesce(ex.followers_count, SmartSetMember.followers_count),
                },
            )
            await session.execute(stmt)
        await session.commit()
    return len(rows)


async def coingecko_handles(limit: int = 2000, *, polite_delay: float = 1.5) -> list[str]:
    """Fetch up to ``limit`` project X handles from CoinGecko (free tier, rate-limited).

    coins/list gives ids; the twitter handle lives on the per-coin detail, so this is
    one polite call per coin. Run incrementally; cache results. Returns lowercased handles.
    """
    handles: list[str] = []
    async with httpx.AsyncClient(base_url=COINGECKO, timeout=30.0) as client:
        coins = (await client.get("/coins/list")).json()
        for coin in coins[:limit]:
            try:
                detail = (
                    await client.get(
                        f"/coins/{coin['id']}",
                        params={"localization": "false", "tickers": "false",
                                "market_data": "false", "community_data": "false",
                                "developer_data": "false"},
                    )
                ).json()
            except httpx.HTTPError as e:  # rate limit / transient -> back off, skip
                logger.warning("coingecko %s: %s", coin.get("id"), e)
                await asyncio.sleep(polite_delay * 4)
                continue
            handle = (detail.get("links", {}) or {}).get("twitter_screen_name")
            if handle:
                handles.append(handle.lower().lstrip("@"))
            await asyncio.sleep(polite_delay)
    logger.info("CoinGecko -> %d project handles", len(handles))
    return handles


def handles_from_csv(path: str, *, link_col: int = 1, skip_rows: int = 1) -> list[str]:
    """Extract X handles from a CSV of accounts (e.g. the AnkhLabs 1.5K KOL sheet:
    ``link_col=1`` X-Links column, ``skip_rows=2`` for its two-row header).

    Dedupes, lowercases, drops non-profile links. Pure/offline — no API spend."""
    seen: set[str] = set()
    handles: list[str] = []
    with open(path, encoding="utf-8") as f:
        for i, row in enumerate(csv.reader(f)):
            if i < skip_rows or len(row) <= link_col:
                continue
            handle = username_from_link(row[link_col].strip())
            if handle and handle.lower() not in seen:
                seen.add(handle.lower())
                handles.append(handle)
    logger.info("CSV %s -> %d unique handles", path, len(handles))
    return handles


async def add_handles(
    handles: list[str], *, source: str, category: Category | None = None
) -> int:
    """Resolve handles -> profiles via twitterapi.io and upsert as seed members.

    If ``category`` is given (e.g. PROJECT for CoinGecko), it's applied; otherwise the
    bio/name heuristic decides. Marks rows is_seed=True.
    """
    tw = TwitterAPIClient()
    rows: list[dict] = []
    for handle in handles:
        profile = await tw.get_user_info(handle)
        if not profile:
            continue
        cat = category or categorize(profile.get("name"), profile.get("description"))
        rows.append(
            {
                "user_id": str(profile["id"]),
                "username": profile.get("userName", handle),
                "display_name": profile.get("name"),
                "category": cat.value,
                "is_seed": True,
                "seed_source": source,
                "following_count": profile.get("following"),
                "followers_count": profile.get("followers"),
                "protected": bool(profile.get("protected", False)),
            }
        )
    n = await _upsert_members(rows)
    logger.info("seeded %d members from %s ($%.4f spent)", n, source, tw.usd_spent)
    return n
