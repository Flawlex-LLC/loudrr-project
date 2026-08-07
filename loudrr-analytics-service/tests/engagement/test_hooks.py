"""Spec — POST /v1/hooks/x: the gateway's realtime X push -> eng_call -> WS broadcast.

This is the service's only public WRITE endpoint, so the auth tests are the load-bearing
ones: unauthenticated, anyone could fabricate "@bigKOL called $SCAM" and it would render as
a real signal.
"""
from datetime import datetime, timezone

import httpx
import pytest
from sqlalchemy import select

from app.db.models import SmartSetMember
from app.engagement import models as em

SOL_CA = "9ybu4ArAY9iyGpjh99eYSmjn5tw3Jvyo9aeRFQqy6Ezh"
SECRET = "s3cret-test-token"


def _tweet(tid="t1", text=f"buying {SOL_CA} here", uid="1", uname="ansem"):
    return {
        "id": tid, "text": text,
        "author": {"id": uid, "userName": uname},
        "createdAt": "Tue Jul 15 18:00:00 +0000 2026",
    }


@pytest.fixture
async def client(eng_db, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "live_webhook_secret", SECRET)
    from app.main import app
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, eng_db


async def _seed(SessionLocal, *, with_token=True):
    async with SessionLocal() as s:
        s.add(SmartSetMember(user_id="1", username="ansem", score=5.0))
        if with_token:
            s.add(em.EngToken(contract=SOL_CA, chain="solana", symbol="PENGU", name="Pengu",
                              price_usd=0.03, pool_address="P1", network="solana",
                              refreshed_at=datetime.now(timezone.utc).replace(tzinfo=None)))
        await s.commit()


# ── auth: fail closed ────────────────────────────────────────────────────────

async def test_push_without_secret_configured_is_refused(eng_db, monkeypatch):
    """Blank secret must 503, NOT accept. An open write endpoint is worse than a dead one."""
    from app.core.config import settings
    monkeypatch.setattr(settings, "live_webhook_secret", "")
    from app.main import app
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post("/v1/hooks/x", json={"tweets": [_tweet()]})
    assert r.status_code == 503
    async with eng_db() as s:
        assert (await s.execute(select(em.EngCall))).scalars().all() == []


async def test_push_with_wrong_secret_is_rejected(client):
    c, SessionLocal = client
    await _seed(SessionLocal)
    r = await c.post("/v1/hooks/x", json={"tweets": [_tweet()]},
                     headers={"X-Loudrr-Secret": "wrong"})
    assert r.status_code == 401
    async with SessionLocal() as s:
        assert (await s.execute(select(em.EngCall))).scalars().all() == []


async def test_push_with_no_header_is_rejected(client):
    c, _ = client
    r = await c.post("/v1/hooks/x", json={"tweets": [_tweet()]})
    assert r.status_code == 401


async def test_non_ascii_secret_is_rejected_not_crashed(client):
    """A non-ASCII secret header must 401, never 500 — hmac.compare_digest raises TypeError on
    a non-ASCII str, which would be an unauthenticated crash of the sole public write endpoint.
    Sent as raw bytes (Starlette decodes header bytes as latin-1), the real attacker's path —
    httpx would otherwise ascii-encode and reject it client-side."""
    c, _ = client
    r = await c.post("/v1/hooks/x", json={"tweets": [_tweet()]},
                     headers=[(b"x-loudrr-secret", b"\xe9\xff-junk")])
    assert r.status_code == 401


# ── ingest ───────────────────────────────────────────────────────────────────

async def test_push_stores_call_and_reports_live(client):
    c, SessionLocal = client
    await _seed(SessionLocal)
    r = await c.post("/v1/hooks/x", json={"tweets": [_tweet()]},
                     headers={"X-Loudrr-Secret": SECRET})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "stored": 1, "live": 1}
    async with SessionLocal() as s:
        call = (await s.execute(select(em.EngCall))).scalars().one()
    assert call.member_id == "1" and call.token_contract == SOL_CA
    assert call.confidence == "contract"


async def test_push_is_idempotent(client):
    """The gateway retries, and the hourly crawl re-derives the same call from the same
    tweet — the second delivery must not double-count it."""
    c, SessionLocal = client
    await _seed(SessionLocal)
    h = {"X-Loudrr-Secret": SECRET}
    first = await c.post("/v1/hooks/x", json={"tweets": [_tweet()]}, headers=h)
    second = await c.post("/v1/hooks/x", json={"tweets": [_tweet()]}, headers=h)
    assert first.json()["stored"] == 1
    assert second.json()["stored"] == 0
    async with SessionLocal() as s:
        assert len((await s.execute(select(em.EngCall))).scalars().all()) == 1


async def test_push_from_untracked_author_is_ignored(client):
    """Anyone can tweet a contract; only tracked smart accounts are signal."""
    c, SessionLocal = client
    await _seed(SessionLocal)
    r = await c.post("/v1/hooks/x",
                     json={"tweets": [_tweet(uid="999", uname="randomguy")]},
                     headers={"X-Loudrr-Secret": SECRET})
    assert r.json()["stored"] == 0


async def test_unresolved_token_is_stored_but_not_broadcast(client):
    """Unresolved calls never reach the UI — so there's nothing to broadcast, but the row
    is kept for the enrichment pass to resolve later."""
    c, SessionLocal = client
    await _seed(SessionLocal, with_token=False)
    r = await c.post("/v1/hooks/x", json={"tweets": [_tweet()]},
                     headers={"X-Loudrr-Secret": SECRET})
    assert r.json() == {"ok": True, "stored": 1, "live": 0}
    async with SessionLocal() as s:
        assert (await s.execute(select(em.EngCall))).scalars().one().token_contract is None


async def test_pure_retweet_is_not_a_call(client):
    """Amplifying someone else's call is engagement, not the member's own call — same rule
    the batch extractor already enforces."""
    c, SessionLocal = client
    await _seed(SessionLocal)
    t = _tweet()
    t["retweeted_tweet"] = {"id": "orig"}
    r = await c.post("/v1/hooks/x", json={"tweets": [t]}, headers={"X-Loudrr-Secret": SECRET})
    assert r.json()["stored"] == 0


async def test_broadcast_reaches_a_subscriber(client):
    """End-to-end: a pushed tweet must surface on the token's live socket."""
    from app.engagement.live import hub
    c, SessionLocal = client
    await _seed(SessionLocal)
    q = await hub.subscribe(SOL_CA)
    try:
        await c.post("/v1/hooks/x", json={"tweets": [_tweet()]},
                     headers={"X-Loudrr-Secret": SECRET})
        frame = q.get_nowait()
        assert frame["type"] == "call" and frame["contract"] == SOL_CA
        assert frame["call"]["username"] == "ansem" and frame["call"]["tweetId"] == "t1"
    finally:
        await hub.unsubscribe(SOL_CA, q)


# ── payload shape tolerance ──────────────────────────────────────────────────

@pytest.mark.parametrize("body", [
    {"tweets": [_tweet()]},
    {"data": [_tweet()]},
    {"tweet": _tweet()},
    [_tweet()],
    _tweet(),
])
async def test_accepts_the_shapes_the_gateway_sends(client, body):
    """Their webhook contract isn't versioned; hard-coding one shape would silently drop
    every push the day it drifts."""
    c, SessionLocal = client
    await _seed(SessionLocal)
    r = await c.post("/v1/hooks/x", json=body, headers={"X-Loudrr-Secret": SECRET})
    assert r.json()["stored"] == 1


async def test_garbage_payload_never_errors(client):
    """A webhook that 500s gets retried or disabled by the sender — worse than dropping it."""
    c, SessionLocal = client
    await _seed(SessionLocal)
    for body in ({"nonsense": True}, {"tweets": "not-a-list"}, [], {}):
        r = await c.post("/v1/hooks/x", json=body, headers={"X-Loudrr-Secret": SECRET})
        assert r.status_code == 200 and r.json()["stored"] == 0
