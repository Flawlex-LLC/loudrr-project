"""Hardening: a banned account is locked out everywhere money or engagement
moves — not just at /session/start/. Django enforces the ban in scattered view
code; here it's enforced consistently at click, submit, queue-claim, and (as a
race backstop) inside settlement itself.
"""
from decimal import Decimal


from app.models.engagement import Engagement
from app.models.post import Post
from app.services import settlement
from app.services.verification import VResult


async def _post(db, owner_id, **kw):
    p = Post(
        user_id=owner_id,
        x_link="https://x.com/owner/status/123",
        escrow=Decimal(kw.pop("escrow", "50")),
        initial_escrow=Decimal(kw.pop("initial_escrow", "50")),
        status=kw.pop("status", "active"),
        platform="web",
        **kw,
    )
    db.add(p)
    await db.commit()
    return p


async def test_banned_cannot_click(client, make_user, db_session):
    owner = await make_user(telegram_id=8101)
    await make_user(telegram_id=8102, is_banned=True)
    post = await _post(db_session, owner.id)
    r = await client.post(
        "/session/click/", params={"telegram_id": 8102}, json={"post_id": str(post.id)}
    )
    assert r.status_code == 403


async def test_banned_cannot_submit(client, make_user):
    await make_user(telegram_id=8103, x_username="me", is_banned=True)
    r = await client.post(
        "/post/submit/", params={"telegram_id": 8103},
        json={"x_link": "https://x.com/me/status/1"},
    )
    assert r.status_code == 403


async def test_banned_cannot_queue_claim(client, make_user):
    await make_user(telegram_id=8104, x_username="me", is_banned=True)
    r = await client.post("/session/queue-claim/", params={"telegram_id": 8104})
    assert r.status_code == 403


async def test_settlement_awards_nothing_to_banned_user(db_session, make_user):
    """A user banned after queueing but before the batch settles earns zero —
    escrow is preserved and the engagement is left untouched."""
    owner = await make_user(telegram_id=8105)
    banned = await make_user(
        telegram_id=8106, x_username="me", is_banned=True,
        tweetscout_score=450,  # would normally earn 1.20x
    )
    post = await _post(db_session, owner.id, escrow="50", initial_escrow="50")
    eng = Engagement(user_id=banned.id, post_id=post.id)
    db_session.add(eng)
    await db_session.commit()
    banned_id, post_id, eng_id = banned.id, post.id, eng.id

    result = await settlement.settle(
        db_session,
        user_id=banned_id,
        results=[VResult(engagement_id=eng_id, post_id=post_id, passed=True)],
    )
    assert result["total_awarded"] == Decimal("0")

    await db_session.refresh(banned)
    await db_session.refresh(post)
    await db_session.refresh(eng)
    assert banned.credits == Decimal("0")          # no credit to a banned user
    assert post.escrow == Decimal("50.0000")       # escrow preserved
    assert eng.credit_granted is False             # engagement untouched
