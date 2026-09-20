"""GET /api/admin/users/{id}/ and its four paginated sub-resources.

The detail view is what an admin reads BEFORE moving someone's balance, so
these tests pin the numbers that decision rests on: the credit block (balance,
lifetime earned/spent and the spendable headroom between them), the activity
counters, the waitlist/referral summary, and the audit trail with the acting
admin's handle joined in.
"""
import uuid
from decimal import Decimal

from app.models.engagement import Engagement
from app.models.post import Post
from app.models.transaction import Transaction, TransactionType
from app.models.waitlist_entry import WaitlistEntry


async def _detail(client, admin, user_id):
    return await client.get(
        f"/api/admin/users/{user_id}/", params={"telegram_id": admin.telegram_id}
    )


async def _make_post(db_session, user, *, escrow="80", status="active"):
    post = Post(
        user_id=user.id, x_link=f"https://x.com/a/status/{uuid.uuid4().int % 10**18}",
        escrow=Decimal(escrow), initial_escrow=Decimal(escrow),
        status=status, platform="telegram",
    )
    db_session.add(post)
    await db_session.commit()
    return post


# ---------------------------------------------------------------------------
# RBAC + 404
# ---------------------------------------------------------------------------
async def test_detail_unauthenticated_401(client, make_user):
    target = await make_user()
    r = await client.get(f"/api/admin/users/{target.id}/")
    assert r.status_code == 401


async def test_detail_regular_user_403(client, make_user):
    user = await make_user(role="")
    target = await make_user()
    r = await _detail(client, user, target.id)
    assert r.status_code == 403


async def test_detail_unknown_user_404(client, make_user):
    admin = await make_user(role="admin")
    r = await _detail(client, admin, uuid.uuid4())
    assert r.status_code == 404


async def test_subresources_require_admin(client, make_user):
    user = await make_user(role="")
    target = await make_user()
    for tail in ("transactions", "posts", "engagements", "audit"):
        r = await client.get(
            f"/api/admin/users/{target.id}/{tail}/",
            params={"telegram_id": user.telegram_id},
        )
        assert r.status_code == 403, tail


# ---------------------------------------------------------------------------
# The payload
# ---------------------------------------------------------------------------
async def test_detail_shape_and_identity(client, make_user):
    from datetime import datetime

    admin = await make_user(role="admin")
    target = await make_user(
        telegram_id=900101, telegram_username="seed_alice", x_username="alice_x",
        display_name="Alice Wonderland", referral_code="REFALICE",
        credits=Decimal("40"), total_credits_earned=Decimal("100"),
        total_credits_spent=Decimal("60"), tweetscout_score=587.2,
        tweetscout_last_updated=datetime(2026, 9, 1, 12, 0),
        role="admin", is_whitelisted=True, x_verified=True,
        total_engagements=7, total_posts=2, current_streak=3, longest_streak=5,
        sponsored_xp=15, total_sponsored_xp_earned=20, sponsored_engagements=4,
    )
    r = await _detail(client, admin, target.id)
    assert r.status_code == 200, r.text
    d = r.json()

    for top in ("id", "telegram_id", "telegram_username", "x_username",
                "display_name", "referral_code", "created_at", "flags",
                "score", "credits", "xp", "activity", "waitlist", "referrals"):
        assert top in d, f"missing {top!r}"

    assert d["display_name"] == "Alice Wonderland"
    assert d["telegram_id"] == 900101
    assert d["flags"]["role"] == "admin"
    assert d["flags"]["is_whitelisted"] is True
    assert d["flags"]["x_verified"] is True
    assert d["score"]["tweetscout_score"] == 587.2
    assert d["score"]["tier"] == "Based"
    assert d["score"]["multiplier"] >= 1
    assert d["xp"]["sponsored_xp"] == 15
    assert d["activity"]["total_engagements"] == 7
    assert d["activity"]["longest_streak"] == 5
    assert d["referrals"]["code"] == "REFALICE"


async def test_spendable_headroom_is_min_of_balance_and_ledger_room(client, make_user):
    """min(credits, earned - spent). A balance above that room cannot be spent,
    which is exactly the drift this field exists to make visible."""
    admin = await make_user(role="admin")

    # room (100-60=40) is BELOW the balance (90) → headroom is 40
    tight = await make_user(
        credits=Decimal("90"), total_credits_earned=Decimal("100"),
        total_credits_spent=Decimal("60"),
    )
    d = (await _detail(client, admin, tight.id)).json()
    assert d["credits"]["balance"] == 90.0
    assert d["credits"]["spendable_headroom"] == 40.0

    # room (100) is ABOVE the balance (25) → headroom is the balance
    loose = await make_user(
        credits=Decimal("25"), total_credits_earned=Decimal("100"),
        total_credits_spent=Decimal("0"),
    )
    d2 = (await _detail(client, admin, loose.id)).json()
    assert d2["credits"]["spendable_headroom"] == 25.0


async def test_detail_counts_posts_engagements_and_escrow(client, make_user, db_session):
    admin = await make_user(role="admin")
    target = await make_user()
    await _make_post(db_session, target, escrow="80", status="active")
    await _make_post(db_session, target, escrow="0", status="completed")
    other = await make_user()
    engaged_post = await _make_post(db_session, other, escrow="50")
    db_session.add(Engagement(user_id=target.id, post_id=engaged_post.id))
    await db_session.commit()

    d = (await _detail(client, admin, target.id)).json()
    assert d["activity"]["posts_active"] == 1
    assert d["activity"]["posts_completed"] == 1
    assert d["activity"]["engagements_recorded"] == 1
    assert d["activity"]["engagements_pending"] == 1
    assert d["credits"]["escrow_locked"] == 80.0


async def test_detail_includes_waitlist_and_referral_summary(client, make_user, db_session):
    admin = await make_user(role="admin")
    target = await make_user(telegram_id=900202)
    entry = WaitlistEntry(
        telegram_id=900202, x_username="alice_x", referral_code="WL0001",
        referral_code_used="FRIEND1", status="approved", region="europe", niche="defi",
        created_user_id=target.id,
    )
    db_session.add(entry)
    await db_session.commit()
    # WaitlistEntry.referrer_id FKs to users.id — the referrer is the USER
    db_session.add(WaitlistEntry(
        telegram_id=900203, x_username="ref1", referral_code="WL0002",
        referrer_id=target.id,
    ))
    await db_session.commit()

    d = (await _detail(client, admin, target.id)).json()
    assert d["waitlist"] is not None
    assert d["waitlist"]["status"] == "approved"
    assert d["waitlist"]["region"] == "europe"
    assert d["referrals"]["referred_by_code"] == "FRIEND1"
    assert d["referrals"]["referrals_made"] == 1


async def test_detail_waitlist_null_when_no_entry(client, make_user):
    admin = await make_user(role="admin")
    target = await make_user()
    d = (await _detail(client, admin, target.id)).json()
    assert d["waitlist"] is None


# ---------------------------------------------------------------------------
# Sub-resources: {rows, total} + paging
# ---------------------------------------------------------------------------
async def test_transactions_paginate_newest_first(client, make_user, db_session):
    admin = await make_user(role="admin")
    target = await make_user(credits=Decimal("30"), total_credits_earned=Decimal("30"))
    for i in range(5):
        db_session.add(Transaction(
            user_id=target.id, type=TransactionType.EARNED,
            amount=Decimal(str(i + 1)), balance_after=Decimal(str(i + 1)),
            idempotency_key=f"k{i}", description=f"earn {i}",
        ))
    await db_session.commit()

    r = await client.get(
        f"/api/admin/users/{target.id}/transactions/",
        params={"telegram_id": admin.telegram_id, "limit": 2, "offset": 0},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 5
    assert len(body["rows"]) == 2
    assert body["rows"][0]["type"] == "earned"
    assert "balance_after" in body["rows"][0]

    page2 = (await client.get(
        f"/api/admin/users/{target.id}/transactions/",
        params={"telegram_id": admin.telegram_id, "limit": 2, "offset": 2},
    )).json()
    ids = {row["id"] for row in body["rows"]}
    assert ids.isdisjoint({row["id"] for row in page2["rows"]})


async def test_posts_subresource(client, make_user, db_session):
    admin = await make_user(role="admin")
    target = await make_user()
    post = await _make_post(db_session, target, escrow="80")
    other = await make_user()
    db_session.add(Engagement(user_id=other.id, post_id=post.id))
    await db_session.commit()

    body = (await client.get(
        f"/api/admin/users/{target.id}/posts/",
        params={"telegram_id": admin.telegram_id},
    )).json()
    assert body["total"] == 1
    [row] = body["rows"]
    assert row["status"] == "active"
    assert row["escrow"] == 80.0
    assert row["engagements"] == 1


async def test_engagements_subresource_joins_the_post(client, make_user, db_session):
    admin = await make_user(role="admin")
    target = await make_user()
    poster = await make_user()
    post = await _make_post(db_session, poster, escrow="50")
    post.tweet_author_username = "poster_handle"
    db_session.add(Engagement(user_id=target.id, post_id=post.id))
    await db_session.commit()

    body = (await client.get(
        f"/api/admin/users/{target.id}/engagements/",
        params={"telegram_id": admin.telegram_id},
    )).json()
    assert body["total"] == 1
    [row] = body["rows"]
    assert row["post_id"] == str(post.id)
    assert row["post_author"] == "poster_handle"
    assert row["verified"] is False


async def test_audit_subresource_joins_actor_handle(client, make_user, db_session):
    """An actor uuid alone tells nobody anything — join the admin's handle."""
    admin = await make_user(role="superadmin", telegram_username="boss_admin")
    target = await make_user(credits=Decimal("10"), total_credits_earned=Decimal("10"))

    await client.post(
        f"/api/admin/users/{target.id}/grant-credits/",
        params={"telegram_id": admin.telegram_id},
        json={"amount": "5", "description": "welcome"},
    )
    await client.post(
        f"/api/admin/users/{target.id}/ban/",
        params={"telegram_id": admin.telegram_id}, json={"reason": "spam"},
    )

    body = (await client.get(
        f"/api/admin/users/{target.id}/audit/",
        params={"telegram_id": admin.telegram_id},
    )).json()
    assert body["total"] == 2
    actions = {row["action"] for row in body["rows"]}
    assert actions == {"grant_credits", "ban_user"}
    for row in body["rows"]:
        assert row["actor_handle"] == "boss_admin"
        assert row["actor_role"] == "superadmin"
        assert row["created_at"]
    grant = next(r for r in body["rows"] if r["action"] == "grant_credits")
    assert grant["detail"]["description"] == "welcome"
