"""Admin → Operations (app/api/admin_ops.py): health, claim batches, failed
notifications, the audit trail and post moderation."""
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.time_utils import utcnow
from app.integrations.x_stream import GatewayUnavailable, get_x_stream_client
from app.models.audit_log import AuditLog
from app.models.outbox_event import OutboxEvent
from app.models.post import Post
from app.models.verification_batch import VerificationBatch


class FakeGateway:
    configured = True

    def __init__(self, credits=5000, fail=False):
        self.credits, self.fail = credits, fail

    async def account_info(self):
        if self.fail:
            raise GatewayUnavailable("HTTP 402 no credits")
        return {"status": "success", "recharge_credits": self.credits}


@pytest.fixture
def gateway(client):
    from app.api import admin_ops
    from app.main import app

    admin_ops._gateway_cache.update(at=0.0, value=None)
    fake = FakeGateway()
    app.dependency_overrides[get_x_stream_client] = lambda: fake
    return fake


def _as(user):
    return {"params": {"telegram_id": user.telegram_id}}


async def _batch(db, user, status, *, minutes_old=1, engagements=3):
    b = VerificationBatch(
        user_id=user.id, engagement_ids=[], status=status,
        created_at=utcnow() - timedelta(minutes=minutes_old),
    )
    b.engagement_ids = [str(i) for i in range(engagements)]
    db.add(b)
    await db.commit()
    return b


async def _post(db, owner, *, status="active", escrow="40", platform="web", author="alice"):
    p = Post(
        user_id=owner.id, x_link=f"https://x.com/{author}/status/{utcnow().timestamp()}",
        tweet_author_username=author, tweet_text=f"gm from {author}",
        escrow=Decimal(escrow), initial_escrow=Decimal(escrow), status=status, platform=platform,
        is_sponsored=platform == "sponsor",
    )
    db.add(p)
    await db.commit()
    return p


# ---- RBAC ----
async def test_ops_needs_admin_and_writes_need_superadmin(client, make_user, db_session, gateway):
    regular = await make_user(role="")
    admin = await make_user(role="admin")
    assert (await client.get("/api/admin/ops/health/", **_as(regular))).status_code == 403
    assert (await client.get("/api/admin/ops/health/", **_as(admin))).status_code == 200
    batch = await _batch(db_session, admin, "failed")
    post = await _post(db_session, admin)
    assert (await client.post(f"/api/admin/ops/batches/{batch.id}/requeue/", **_as(admin))).status_code == 403
    assert (await client.post(f"/api/admin/ops/posts/{post.id}/cancel/", json={}, **_as(admin))).status_code == 403


# ---- health ----
async def test_health_reports_gateway_credits_held_batches_and_failed_notifications(
    client, make_user, db_session, gateway,
):
    admin = await make_user(role="admin")
    await _batch(db_session, admin, "pending", minutes_old=2)          # just queued
    await _batch(db_session, admin, "pending", minutes_old=60 * 14)    # held 14h
    await _batch(db_session, admin, "failed", minutes_old=60)
    db_session.add(OutboxEvent(event_type="admin_ban", status="failed", payload={"telegram_id": 1}))
    await db_session.commit()

    body = (await client.get("/api/admin/ops/health/", **_as(admin))).json()
    assert body["gateway"] == {**body["gateway"], "configured": True, "reachable": True, "credits": 5000}
    assert body["batches"]["pending"] == 2
    assert body["batches"]["held"] == 1
    assert body["batches"]["failed"] == 1 and body["batches"]["failed_24h"] == 1
    assert body["batches"]["oldest_waiting_minutes"] >= 60 * 14 - 1
    assert body["outbox"]["failed"] == 1


async def test_health_shows_an_unreachable_gateway_instead_of_failing(client, make_user, gateway):
    admin = await make_user(role="admin")
    gateway.fail = True
    body = (await client.get("/api/admin/ops/health/", **_as(admin))).json()
    assert body["gateway"]["reachable"] is False
    assert "no credits" in body["gateway"]["error"]


# ---- batches ----
async def test_batches_filter_held_and_requeue_a_failed_batch(client, make_user, db_session, gateway):
    boss = await make_user(role="superadmin", telegram_username="boss")
    held = await _batch(db_session, boss, "processing", minutes_old=600)
    fresh = await _batch(db_session, boss, "pending", minutes_old=1)
    failed = await _batch(db_session, boss, "failed", minutes_old=60 * 24 * 5)

    items = (await client.get("/api/admin/ops/batches/", params={"status": "held", "telegram_id": boss.telegram_id})).json()["items"]
    assert [i["id"] for i in items] == [str(held.id)]
    assert items[0]["held"] is True and items[0]["user_handle"] == "boss"

    r = await client.post(f"/api/admin/ops/batches/{failed.id}/requeue/", **_as(boss))
    assert r.status_code == 200 and r.json()["status"] == "pending"
    await db_session.refresh(failed)
    assert failed.status == "pending"
    log = (await db_session.execute(select(AuditLog).where(AuditLog.action == "requeue_batch"))).scalar_one()
    assert log.detail == {"previous_status": "failed"} and log.actor_id == boss.id

    fresh.status = "completed"
    await db_session.commit()
    assert (await client.post(f"/api/admin/ops/batches/{fresh.id}/requeue/", **_as(boss))).status_code == 409


# ---- notifications ----
async def test_retry_a_failed_notification(client, make_user, db_session, gateway):
    admin = await make_user(role="admin")
    dead = OutboxEvent(event_type="waitlist_rejected", status="failed", retry_count=3,
                       error_message="bot blocked", payload={"telegram_id": 42})
    sent = OutboxEvent(event_type="waitlist_approved", status="sent", payload={"telegram_id": 43})
    db_session.add_all([dead, sent])
    await db_session.commit()

    items = (await client.get("/api/admin/ops/outbox/", **_as(admin))).json()["items"]
    assert [i["id"] for i in items] == [str(dead.id)]          # default view: failed only
    assert items[0]["error_message"] == "bot blocked" and items[0]["telegram_id"] == 42

    r = await client.post(f"/api/admin/ops/outbox/{dead.id}/retry/", **_as(admin))
    assert r.status_code == 200
    await db_session.refresh(dead)
    assert dead.status == "pending" and dead.retry_count == 0
    assert (await client.post(f"/api/admin/ops/outbox/{sent.id}/retry/", **_as(admin))).status_code == 409


# ---- audit ----
async def test_audit_log_filters_and_names_the_actor(client, make_user, db_session, gateway):
    admin = await make_user(role="admin", telegram_username="ops_anna")
    db_session.add_all([
        AuditLog(actor_id=admin.id, action="ban_user", target_type="user", detail={"reason": "spam"}),
        AuditLog(actor_id=admin.id, action="grant_credits", target_type="user", detail={"amount": "5"}),
    ])
    await db_session.commit()
    body = (await client.get("/api/admin/ops/audit/", params={"action": "ban_user", "telegram_id": admin.telegram_id})).json()
    assert body["total"] == 1
    assert body["items"][0]["actor_handle"] == "ops_anna"
    assert body["items"][0]["detail"] == {"reason": "spam"}
    assert {"ban_user", "grant_credits"} <= set(body["actions"])
    by_actor = (await client.get("/api/admin/ops/audit/", params={"actor": "@OPS_ANNA", "telegram_id": admin.telegram_id})).json()
    assert by_actor["total"] == 2


# ---- posts ----
async def test_cancel_a_post_refunds_the_poster(client, make_user, db_session, gateway):
    boss = await make_user(role="superadmin")
    poster = await make_user(credits=Decimal("0"), total_credits_earned=Decimal("40"),
                             total_credits_spent=Decimal("40"))
    post = await _post(db_session, poster, escrow="40", author="scammer")

    found = (await client.get("/api/admin/ops/posts/", params={"q": "@Scammer", "telegram_id": boss.telegram_id})).json()
    assert [i["id"] for i in found["items"]] == [str(post.id)]

    r = await client.post(f"/api/admin/ops/posts/{post.id}/cancel/", json={"reason": "phishing link"}, **_as(boss))
    assert r.status_code == 200 and r.json()["refunded"] == 40.0
    await db_session.refresh(post)
    await db_session.refresh(poster)
    assert post.status == "cancelled" and post.escrow == 0
    assert poster.credits == Decimal("40")
    log = (await db_session.execute(select(AuditLog).where(AuditLog.action == "cancel_post"))).scalar_one()
    assert log.detail["reason"] == "phishing link"
    assert (await client.post(f"/api/admin/ops/posts/{post.id}/cancel/", json={}, **_as(boss))).status_code == 409


async def test_cancelling_a_sponsored_post_refunds_nobody(client, make_user, db_session, gateway):
    from app.services.sponsors import platform_user

    boss = await make_user(role="superadmin")
    platform = await platform_user(db_session)
    await db_session.commit()
    post = await _post(db_session, platform, escrow="100", platform="sponsor", author="brand")
    r = await client.post(f"/api/admin/ops/posts/{post.id}/cancel/", json={}, **_as(boss))
    assert r.status_code == 200 and r.json()["refunded"] == 0.0
    await db_session.refresh(platform)
    assert platform.credits == Decimal("0")
