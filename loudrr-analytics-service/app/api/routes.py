"""Public scoring API — Sorsa-compatible shape (research §2, §11 step 5).

Every endpoint takes one of ?user_link | ?username | ?user_id, exactly like Sorsa.
Reads are served entirely from the precomputed reverse index + member scores — no
scraping at query time (research §7). Case-3 (no smart followers) returns score 0
truthfully rather than erroring.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select

from app.api.auth import require_api_key
from app.core.loudrr_score import loudrr_score
from app.db.models import Edge, ProfileCache, RankedAccount, SmartSetMember
from app.db.models import _utcnow
from app.db.session import SessionLocal
from app.services import score as scoring
from app.services.resolve import resolve_user_id

logger = logging.getLogger("api.routes")
router = APIRouter()

# Profile metadata (followers/pic/bio) is cached this long; the SCORE is always recomputed
# live from our own graph, so it's never stale even when the profile card is served from cache.
PROFILE_TTL = timedelta(hours=24)


# ---- public real-data serving (ranked_accounts, imported crawl output) -------

def _acct_item(a: RankedAccount) -> dict:
    return {
        "rank": a.rank,
        "userName": a.display_username or a.username,
        "name": a.name,
        "score": a.score,
        "eliteFollowers": a.elite_followers,
        "followers": a.followers,
        "verified": bool(a.verified),
        "categories": a.categories,
    }


@router.get("/leaderboard")
async def get_leaderboard(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    """The real ranked board — served straight from the crawl's computed ranking."""
    async with SessionLocal() as s:
        total = (await s.execute(select(func.count()).select_from(RankedAccount))).scalar() or 0
        rows = (await s.execute(
            select(RankedAccount).order_by(RankedAccount.rank).offset(offset).limit(limit)
        )).scalars().all()
    return {"total": int(total), "items": [_acct_item(a) for a in rows]}


async def _notable_followers(s, user_id: str) -> list[dict]:
    """Highest-scored smart accounts that ACTUALLY follow X, from the real follow graph
    (edges.follower_id is always a smart-set member)."""
    rows = (await s.execute(
        select(RankedAccount)
        .join(Edge, Edge.follower_id == RankedAccount.user_id)
        .where(Edge.followee_id == user_id)
        .order_by(RankedAccount.score.desc()).limit(12)
    )).scalars().all()
    return [{
        "userName": f.display_username or f.username, "name": f.name,
        "score": f.score, "rank": f.rank, "categories": f.categories,
    } for f in rows]


def _card_dict(row: ProfileCache) -> dict:
    """ProfileCache -> plain dict so we can use it after the session closes."""
    return {
        "user_id": row.user_id,
        "display_username": row.display_username, "name": row.name, "bio": row.bio,
        "followers": row.followers, "following": row.following,
        "verified": row.verified, "image": row.image,
    }


async def _resolve_and_cache(uname: str) -> dict | None:
    """Resolve + cache a non-ranked handle with a SINGLE gateway call. Returns a card dict
    (incl. user_id) or None if the handle doesn't exist on X. smart_set members short-circuit
    the id lookup but still get one gateway call to populate the display card once."""
    async with SessionLocal() as s:
        member = (await s.execute(
            select(SmartSetMember).where(func.lower(SmartSetMember.username) == uname).limit(1)
        )).scalars().first()
    known_uid = member.user_id if member else None

    try:
        from app.clients.twitterapi import TwitterAPIClient
        p = await TwitterAPIClient().get_user_info(uname)
    except Exception:  # noqa: BLE001 — gateway down
        logger.warning("profile fetch failed for @%s", uname)
        p = None

    if not p:
        # gateway miss: a known member still resolves (minimal card); otherwise unknown
        if known_uid:
            return {"user_id": known_uid, "display_username": member.display_name or uname,
                    "name": member.display_name, "bio": None, "followers": None,
                    "following": None, "verified": False, "image": None}
        return None

    uid = known_uid or str(p.get("id") or "")
    if not uid:
        return None
    fields = {
        "username": uname,
        "display_username": p.get("userName") or uname,
        "name": p.get("name"),
        "bio": (p.get("description") or "")[:1024] or None,
        "followers": p.get("followers"),
        "following": p.get("following"),
        "verified": bool(p.get("isBlueVerified") or p.get("isVerified")),
        "image": (p.get("profilePicture") or "")[:512] or None,
        "location": (p.get("location") or "")[:128] or None,
        "refreshed_at": _utcnow(),
    }
    try:
        async with SessionLocal() as s:
            row = await s.get(ProfileCache, uid)
            if row is None:
                row = ProfileCache(user_id=uid, **fields)
                s.add(row)
            else:
                for k, v in fields.items():
                    setattr(row, k, v)
            await s.commit()
            return _card_dict(row)
    except Exception:  # noqa: BLE001 — cache table absent/transient: serve uncached this time
        logger.warning("profile_cache write failed for @%s (serving uncached)", uname)
        return {"user_id": uid, "display_username": fields["display_username"],
                "name": fields["name"], "bio": fields["bio"], "followers": fields["followers"],
                "following": fields["following"], "verified": fields["verified"],
                "image": fields["image"]}


@router.get("/profile")
async def get_profile(user_name: str = Query("", alias="userName")):
    """Profile card for ANY X account. Ranked accounts serve from the crawl table; everyone
    else is resolved + scored on the fly (score_for over our own graph — always fresh) with a
    24h-cached profile card. No real account ever shows "not scored"."""
    uname = user_name.strip().lstrip("@").lower()
    if not uname:
        return {"found": False, "userName": uname}

    async with SessionLocal() as s:
        total = (await s.execute(select(func.count()).select_from(RankedAccount))).scalar() or 1

        # fast path: a fully-ranked account
        a = (await s.execute(
            select(RankedAccount).where(RankedAccount.username == uname).limit(1)
        )).scalars().first()
        if a is not None:
            return {
                "found": True, **_acct_item(a), "userId": a.user_id, "bio": a.bio,
                "following": a.following, "raw": a.raw_score,
                "percentile": round((1 - (a.rank - 1) / max(1, total)) * 100, 2),
                "totalRanked": int(total),
                "notableFollowers": await _notable_followers(s, a.user_id),
            }

    # long tail: cache-by-username first (a fresh card gives us the user_id with ZERO gateway
    # calls), else resolve + fetch once. Score is ALWAYS recomputed below — never cached.
    uid: str | None = None
    card: dict | None = None
    try:
        async with SessionLocal() as s:
            cached = (await s.execute(
                select(ProfileCache).where(ProfileCache.username == uname).limit(1))).scalars().first()
            if cached is not None and (_utcnow() - cached.refreshed_at) < PROFILE_TTL:
                uid, card = cached.user_id, _card_dict(cached)
    except Exception:  # noqa: BLE001 — cache table absent/transient: fall through to resolve
        logger.warning("profile_cache read failed (continuing uncached)")

    if uid is None:
        card = await _resolve_and_cache(uname)         # single gateway call, cached
        if not card:
            return {"found": False, "userName": uname}   # handle genuinely doesn't exist on X
        uid = card["user_id"]

    res = await scoring.score_for(uid)                # our own graph — no gateway, always fresh
    raw = res.get("raw", 0.0)
    score = loudrr_score(raw)
    async with SessionLocal() as s:
        higher = (await s.execute(
            select(func.count()).select_from(RankedAccount)
            .where(RankedAccount.score > score))).scalar() or 0
        notable = await _notable_followers(s, uid)

    card = card or {}
    return {
        "found": True,
        "rank": int(higher) + 1,
        "userName": card.get("display_username") or uname,
        "name": card.get("name"),
        "score": score,
        "raw": raw,
        "eliteFollowers": int(res.get("smart_followers", 0)),
        "followers": card.get("followers"),
        "following": card.get("following"),
        "verified": bool(card.get("verified")),
        "categories": None,
        "bio": card.get("bio"),
        "image": card.get("image"),
        "percentile": round((1 - higher / max(1, total)) * 100, 2),
        "totalRanked": int(total),
        "userId": uid,
        "notableFollowers": notable,
    }


@router.get("/search")
async def search_accounts(q: str = Query("")):
    """Typeahead over the ranked universe (username + display name), best rank first."""
    q = q.strip().lstrip("@").lower()
    if len(q) < 2:
        return {"items": []}
    like = f"%{q}%"
    async with SessionLocal() as s:
        rows = (await s.execute(
            select(RankedAccount)
            .where(or_(RankedAccount.username.like(like),
                       func.lower(RankedAccount.name).like(like)))
            .order_by(RankedAccount.rank).limit(8)
        )).scalars().all()
    return {"items": [_acct_item(a) for a in rows]}


async def _target(user_link: str | None, username: str | None, user_id: str | None) -> str:
    uid, _ = await resolve_user_id(user_id=user_id, username=username, user_link=user_link)
    if not uid:
        raise HTTPException(400, "provide one of user_link, username, or user_id")
    return uid


# common query params (accept Sorsa-style snake_case + the frontend's camelCase userName)
_link = Query(None, alias="user_link")
_name = Query(None, alias="username")
_uid = Query(None, alias="user_id")
_camel = Query(None, alias="userName")


@router.get("/score", dependencies=[Depends(require_api_key)])
async def get_score(
    user_link: str | None = _link,
    username: str | None = _name,
    user_id: str | None = _uid,
    user_name: str | None = _camel,
):
    """Public, keyless Loudrr Score lookup. Returns the calibrated 0–6000 Loudrr Score
    (the marketing-funnel number) plus a few supporting fields."""
    uid = await _target(user_link, username or user_name, user_id)
    result = await scoring.score_for(uid)

    # the headline number: locked Loudrr Score calibration (raw -> 0..6000)
    from app.core.loudrr_score import loudrr_score

    result["score"] = loudrr_score(result.get("raw", 0.0))
    result["userName"] = username or user_name
    result["userId"] = uid

    # secondary: parity estimate vs harvested TweetScout/Sorsa, if a fit exists (lazy import)
    from app.services.calibration import apply_calibration

    parity = apply_calibration(result.get("raw", 0.0))
    if parity is not None:
        result["parity_score"] = round(parity, 2)
    return result


@router.get("/score-changes", dependencies=[Depends(require_api_key)])
async def get_score_changes(
    user_link: str | None = _link,
    username: str | None = _name,
    user_id: str | None = _uid,
    user_name: str | None = _camel,
):
    return await scoring.score_changes(await _target(user_link, username or user_name, user_id))


@router.get("/followers-stats", dependencies=[Depends(require_api_key)])
async def get_followers_stats(
    user_link: str | None = _link,
    username: str | None = _name,
    user_id: str | None = _uid,
    user_name: str | None = _camel,
):
    return await scoring.followers_stats(await _target(user_link, username or user_name, user_id))


@router.get("/top-followers", dependencies=[Depends(require_api_key)])
async def get_top_followers(
    user_link: str | None = _link,
    username: str | None = _name,
    user_id: str | None = _uid,
    user_name: str | None = _camel,
    k: int = Query(default=10, ge=1, le=100, description="How many top smart followers to return (default 10, max 100)."),
):
    """Top-N smart-set members who follow the target user, ranked by Loudrr Score.
    ``k`` is exposed as a query param so the miniapp can request 10 (its default UI)
    or bump it up for the deeper 'smart followers' view — no need for the caller to
    slice client-side + waste the extra DB rows on the wire."""
    return {"users": await scoring.top_followers(await _target(user_link, username or user_name, user_id), k=k)}


@router.get("/top-following", dependencies=[Depends(require_api_key)])
async def get_top_following(
    user_link: str | None = _link,
    username: str | None = _name,
    user_id: str | None = _uid,
    user_name: str | None = _camel,
):
    """Top smart accounts that X *follows*, by score. Only available when X's own
    following has been crawled (i.e. X is in the smart set); otherwise empty."""
    uid = await _target(user_link, username or user_name, user_id)
    async with SessionLocal() as session:
        rows = (
            await session.execute(
                scoring._apply_cutoff(
                    select(SmartSetMember)
                    .join(Edge, Edge.followee_id == SmartSetMember.user_id)
                    .where(Edge.follower_id == uid)
                )
                .order_by(SmartSetMember.score.desc())
                .limit(20)
            )
        ).scalars()
        users = [
            {"id": m.user_id, "username": m.username, "display_name": m.display_name,
             "category": m.category, "score": scoring._display(m.score)}
            for m in rows
        ]
    return {"users": users}
