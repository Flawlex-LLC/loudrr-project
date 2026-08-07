"""Realtime KOL-call ingest: the gateway's X push -> eng_call -> our WebSocket.

    gateway x_user_stream --POST--> /v1/hooks/x --> calls_from_tweet --> eng_call
                                                                     \\-> hub broadcast

This is the OFFCHAIN half of realtime ("which KOL called"); the onchain half ("which KOL
purchased") is the trade poller in live.py. The gateway has no WebSocket of its own (verified
against gateway.loudrr.com/docs/openapi.json — 66 routes, zero WS), so push is the only way
to learn about a tweet the moment it lands; the hourly crawl stays as the backstop that
catches anything the push misses.

SECURITY — this is the only public WRITE endpoint in the service. Without authentication
anyone on the internet could POST fabricated "@bigKOL called $SCAM" rows into the product
and they would render as real signals. So it FAILS CLOSED: no LIVE_WEBHOOK_SECRET configured
=> 503 and nothing is written. A wrong secret => 401. Never a silent accept.

Everything else degrades quietly: an unknown author, an unparseable payload or a token we
can't resolve yet is a 200 with {stored: 0} — a webhook that errors gets retried or disabled
by the sender, which is worse than dropping one tweet the hourly crawl will pick up anyway.
"""
from __future__ import annotations

import hmac
import logging
from typing import Any

from fastapi import APIRouter, Header, Request, Response
from sqlalchemy import func, select

from app.core.config import settings
from app.db.models import SmartSetMember
from app.db.session import SessionLocal
from app.engagement.calls import calls_from_tweet
from app.engagement.live import push_call
from app.engagement.models import EngCall, EngToken

logger = logging.getLogger("eng.hooks")

router = APIRouter()

# Bound the work one authenticated push can trigger — a legitimate x_user_stream delivery
# carries a handful of tweets, so anything past this is a misfire or an abuse attempt, not
# signal. Caps DB work regardless of body size.
MAX_TWEETS_PER_PUSH = 100


def _tweets_from(payload: Any) -> list[dict]:
    """Pull the tweet objects out of a gateway push, tolerating shape drift.

    The webhook contract isn't versioned on their side, so we accept the shapes it has been
    seen to use rather than hard-coding one and silently dropping every push the day it
    changes: {tweets:[...]}, {data:[...]}, {tweet:{...}}, or a bare list.
    """
    if isinstance(payload, list):
        return [t for t in payload if isinstance(t, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("tweets", "data", "results", "items"):
        v = payload.get(key)
        if isinstance(v, list):
            return [t for t in v if isinstance(t, dict)]
        if isinstance(v, dict):
            return [v]
    for key in ("tweet", "event", "payload"):
        v = payload.get(key)
        if isinstance(v, dict):
            return [v]
    # a bare tweet object posted at the top level
    return [payload] if payload.get("id") and payload.get("text") is not None else []


async def _member_id_for(s, t: dict) -> str | None:
    """Map a pushed tweet's author onto a tracked member, or None if untracked.

    Prefer the author id — handles get renamed and recycled, so matching on username alone
    would attribute a new owner's post to the previous owner's history.
    """
    author = t.get("author") or {}
    uid = str(author.get("id") or "").strip()
    if uid:
        hit = (await s.execute(
            select(SmartSetMember.user_id).where(SmartSetMember.user_id == uid).limit(1)
        )).scalars().first()
        if hit:
            return str(hit)
    uname = str(author.get("userName") or author.get("username") or "").strip().lstrip("@").lower()
    if uname:
        hit = (await s.execute(
            select(SmartSetMember.user_id)
            .where(func.lower(SmartSetMember.username) == uname).limit(1)
        )).scalars().first()
        if hit:
            return str(hit)
    return None


async def _resolve_locally(s, row: dict) -> str | None:
    """Best-effort token_contract WITHOUT any external call.

    The push path must stay cheap and fast, so it only resolves against tokens we already
    know. Anything else is stored unresolved and the normal enrichment pass fills it in —
    unresolved calls never reach the UI, which is the existing safety rule, not a new one.
    """
    if row["confidence"] == "contract":
        addr = str(row["token_key"])
        hit = await s.get(EngToken, addr)
        if hit is not None:
            return hit.contract
        # contract path is unambiguous; store it even if we haven't enriched the token yet
        return None
    tick = (row.get("ticker") or "").upper()
    if not tick:
        return None
    return (await s.execute(
        select(EngToken.contract).where(func.upper(EngToken.symbol) == tick)
        .order_by(EngToken.liquidity_usd.desc().nulls_last()).limit(1)
    )).scalars().first()


@router.post("/hooks/x")
async def x_push(request: Request, x_loudrr_secret: str = Header("", alias="X-Loudrr-Secret")):
    """Gateway x_user_stream push: store any KOL calls in the tweet and broadcast them live."""
    secret = settings.live_webhook_secret
    if not secret:
        # fail closed: an open write endpoint would let anyone fabricate KOL calls
        logger.error("x webhook hit but LIVE_WEBHOOK_SECRET is unset — refusing the push")
        return Response(status_code=503)
    # constant-time (a plain == leaks the secret one byte at a time under timing analysis),
    # on BYTES: compare_digest raises TypeError on a non-ASCII str, which would turn a junk
    # header into an unauthenticated 500 on the sole public write endpoint.
    if not hmac.compare_digest((x_loudrr_secret or "").encode("utf-8"), secret.encode("utf-8")):
        return Response(status_code=401)

    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001 — malformed body is the sender's bug, not ours
        return {"ok": True, "stored": 0, "reason": "unparseable"}

    stored = 0
    broadcast: list[tuple[str, dict]] = []
    try:
        async with SessionLocal() as s:
            for t in _tweets_from(payload)[:MAX_TWEETS_PER_PUSH]:
                member_id = await _member_id_for(s, t)
                if member_id is None:
                    continue  # not a tracked KOL — nothing to say about it
                for row in calls_from_tweet(t, member_id):
                    # idempotent: the gateway retries, and the hourly crawl will re-derive
                    # this exact call from the same tweet (uq_eng_call: tweet_id+token_key)
                    dup = (await s.execute(
                        select(EngCall.id).where(EngCall.tweet_id == row["tweet_id"],
                                                 EngCall.token_key == row["token_key"]).limit(1)
                    )).scalars().first()
                    if dup:
                        continue
                    contract = await _resolve_locally(s, row)
                    s.add(EngCall(**row, token_contract=contract))
                    stored += 1
                    if contract:
                        uname = str((t.get("author") or {}).get("userName") or "") or None
                        broadcast.append((contract, {
                            "username": uname, "tweetId": row["tweet_id"],
                            "ts": row["ts"].isoformat() if row["ts"] else None,
                            "priceAtCall": None, "confidence": row["confidence"],
                        }))
            await s.commit()
    except Exception:  # noqa: BLE001 — never fail the sender; the crawl is the backstop
        logger.exception("x webhook ingest failed")
        return {"ok": True, "stored": 0, "reason": "error"}

    # broadcast AFTER the commit: a client must never be told about a call that then
    # fails to persist and vanishes on their next refresh
    for contract, call in broadcast:
        await push_call(contract, call)

    return {"ok": True, "stored": stored, "live": len(broadcast)}
