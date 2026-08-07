"""Ch17 — feature-interest endpoint (15) + admin operations (audit-logged)."""
from decimal import Decimal

from sqlalchemy import select

from app.models.audit_log import AuditLog
from app.models.x_verification_request import XVerificationRequest
from app.services import admin


# ---- endpoint 15: /feature-interest/ ----
async def test_register_then_check_interest(client, make_user):
    await make_user(telegram_id=11001)
    p = {"telegram_id": 11001}

    g0 = await client.get("/feature-interest/", params={**p, "feature": "campaigns"})
    assert g0.json() == {"registered": False}

    post = await client.post(
        "/feature-interest/", params=p,
        json={"feature": "campaigns", "interests": ["raffles", "rewards"]},
    )
    assert post.status_code == 200 and post.json() == {"success": True}

    g1 = await client.get("/feature-interest/", params={**p, "feature": "campaigns"})
    assert g1.json() == {"registered": True}


async def test_register_interest_idempotent_update(client, make_user, db_session):
    await make_user(telegram_id=11002)
    p = {"telegram_id": 11002}
    await client.post("/feature-interest/", params=p, json={"feature": "earn", "interests": ["a"]})
    await client.post("/feature-interest/", params=p, json={"feature": "earn", "interests": ["b"]})
    from app.repositories.feature_interest import FeatureInterestRepository
    rows = await FeatureInterestRepository(db_session).list(limit=10)
    earn = [r for r in rows if r.feature == "earn"]
    assert len(earn) == 1  # one row per (user, feature)
    assert earn[0].interests == ["b"]


async def test_invalid_feature_name_400(client, make_user):
    await make_user(telegram_id=11003)
    r = await client.post(
        "/feature-interest/", params={"telegram_id": 11003}, json={"feature": "bad name!!"}
    )
    assert r.status_code == 400


# ---- admin operations ----
async def test_admin_grant_credits_audited(make_user, db_session):
    admin_user = await make_user(telegram_id=11004)
    target = await make_user(telegram_id=11005, credits=Decimal("0"))
    await admin.grant_credits(db_session, admin_id=admin_user.id, user_id=target.id, amount=500)
    await db_session.refresh(target)
    assert target.credits == Decimal("500")
    logs = (await db_session.execute(select(AuditLog))).scalars().all()
    assert any(log.action == "grant_credits" for log in logs)


async def test_admin_ban_clears_whitelist(make_user, db_session):
    admin_user = await make_user(telegram_id=11006)
    target = await make_user(telegram_id=11007, is_whitelisted=True)
    await admin.ban_user(db_session, admin_id=admin_user.id, user_id=target.id, reason="spam")
    await db_session.refresh(target)
    assert target.is_banned is True
    assert target.is_whitelisted is False  # constraint NOT(banned AND whitelisted)


async def test_admin_revoke_credits(make_user, db_session):
    admin_user = await make_user(telegram_id=11008)
    target = await make_user(telegram_id=11009, credits=Decimal("100"), total_credits_earned=Decimal("100"))
    await admin.revoke_credits(db_session, admin_id=admin_user.id, user_id=target.id, amount=30, reason="penalty")
    await db_session.refresh(target)
    assert target.credits == Decimal("70")


async def test_reject_x_verification(make_user, db_session):
    admin_user = await make_user(telegram_id=11010)
    target = await make_user(telegram_id=11011, x_verified=True, pending_claimed_x_username="other")
    req = XVerificationRequest(
        user_id=target.id, submitted_x_username="a", claimed_x_username="b",
        claimed_x_user_id="99", status="PENDING",
    )
    db_session.add(req)
    await db_session.commit()

    await admin.reject_x_verification(db_session, admin_id=admin_user.id, request_id=req.id, notes="nope")
    await db_session.refresh(req)
    await db_session.refresh(target)
    assert req.status == "REJECTED"
    assert target.x_verified is False
    assert target.pending_claimed_x_username == ""
