"""The admin website: Telegram Login Widget sign-in -> session cookie.

The admin panel is a normal website, not part of the Telegram mini-app. These
tests run with DEBUG=True (the ?telegram_id= dev bypass exists), so each one
exercises the REAL cookie path by simply not passing telegram_id.
"""
import hashlib
import hmac
import time

import pytest
from sqlalchemy import select

from app.core import admin_session
from app.core.config import settings
from app.core.telegram_auth import verify_login_widget
from app.models.audit_log import AuditLog

BOT_TOKEN = "123456:TEST-bot-token"
CSRF = {admin_session.CSRF_HEADER: admin_session.CSRF_VALUE}


@pytest.fixture(autouse=True)
def _bot_token(monkeypatch):
    monkeypatch.setattr(settings, "telegram_bot_token", BOT_TOKEN)


def widget_payload(telegram_id: int, *, username="boss", age_s=0, token=BOT_TOKEN) -> dict:
    """What Telegram's Login Widget hands the page, signed like Telegram does."""
    fields = {"id": str(telegram_id), "first_name": "Boss", "username": username,
              "auth_date": str(int(time.time()) - age_s)}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    fields["hash"] = hmac.new(hashlib.sha256(token.encode()).digest(), check.encode(),
                              hashlib.sha256).hexdigest()
    fields["id"] = int(fields["id"])
    fields["auth_date"] = int(fields["auth_date"])
    return fields


# ---- the signature check itself ----
def test_verify_login_widget_accepts_telegrams_signature():
    assert verify_login_widget(widget_payload(42), BOT_TOKEN)["id"] == "42"


@pytest.mark.parametrize("mutate", [
    lambda p: p.update(id=43),                        # someone else's id
    lambda p: p.update(hash="0" * 64),                # forged
    lambda p: p.update(username="other"),             # tampered field
])
def test_verify_login_widget_rejects_tampering(mutate):
    payload = widget_payload(42)
    mutate(payload)
    with pytest.raises(ValueError):
        verify_login_widget(payload, BOT_TOKEN)


def test_verify_login_widget_rejects_an_old_or_foreign_payload():
    with pytest.raises(ValueError):
        verify_login_widget(widget_payload(42, age_s=600), BOT_TOKEN)     # replayed later
    with pytest.raises(ValueError):
        verify_login_widget(widget_payload(42, token="999:other-bot"), BOT_TOKEN)
    with pytest.raises(ValueError):
        verify_login_widget(widget_payload(42), "")                       # no bot token


# ---- signing in ----
async def test_admin_signs_in_and_the_cookie_opens_the_panel(client, make_user, db_session):
    admin = await make_user(role="admin", telegram_id=71001)
    r = await client.post("/api/admin/auth/telegram/", json=widget_payload(71001))
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "admin"
    cookie = r.cookies.get(admin_session.COOKIE_NAME)
    assert cookie
    set_cookie = r.headers["set-cookie"].lower()
    assert "httponly" in set_cookie and "samesite=strict" in set_cookie

    me = await client.get("/api/admin/me/", cookies={admin_session.COOKIE_NAME: cookie})
    assert me.status_code == 200 and me.json()["id"] == str(admin.id)
    log = (await db_session.execute(select(AuditLog).where(AuditLog.action == "admin_login"))).scalar_one()
    assert log.actor_id == admin.id


@pytest.mark.parametrize("role, banned", [("", False), ("admin", True)])
async def test_non_admins_and_banned_admins_cannot_sign_in(client, make_user, role, banned):
    kwargs = {"role": role, "telegram_id": 71002}
    if banned:
        kwargs.update(is_banned=True, is_whitelisted=False)
    await make_user(**kwargs)
    r = await client.post("/api/admin/auth/telegram/", json=widget_payload(71002))
    assert r.status_code == 403
    assert admin_session.COOKIE_NAME not in r.cookies


async def test_unknown_telegram_account_cannot_sign_in(client):
    r = await client.post("/api/admin/auth/telegram/", json=widget_payload(71999))
    assert r.status_code == 403


async def test_a_bad_signature_cannot_sign_in(client, make_user):
    await make_user(role="superadmin", telegram_id=71003)
    payload = widget_payload(71003)
    payload["hash"] = "f" * 64
    assert (await client.post("/api/admin/auth/telegram/", json=payload)).status_code == 401


# ---- using the session ----
async def test_mini_app_credentials_no_longer_open_admin_routes(client, make_user):
    """Inside Telegram an admin is just a user: initData must not reach /api/admin."""
    await make_user(role="superadmin", telegram_id=71004)
    r = await client.get("/api/admin/me/", headers={"X-Telegram-Init-Data": "user=%7B%22id%22%3A71004%7D&hash=x"})
    assert r.status_code == 401


async def test_writes_need_the_admin_header(client, make_user):
    boss = await make_user(role="superadmin", telegram_id=71005)
    target = await make_user()
    cookie = {admin_session.COOKIE_NAME: admin_session.issue(user_id=str(boss.id), telegram_id=71005)}
    url = f"/api/admin/users/{target.id}/grant-credits/"
    no_header = await client.post(url, json={"amount": "5", "request_id": "r1"}, cookies=cookie)
    assert no_header.status_code == 403
    ok = await client.post(url, json={"amount": "5", "request_id": "r2"}, cookies=cookie, headers=CSRF)
    assert ok.status_code == 200, ok.text


async def test_demotion_ends_access_on_the_next_request(client, make_user, db_session):
    admin = await make_user(role="admin", telegram_id=71006)
    cookie = {admin_session.COOKIE_NAME: admin_session.issue(user_id=str(admin.id), telegram_id=71006)}
    assert (await client.get("/api/admin/me/", cookies=cookie)).status_code == 200
    admin.role = ""
    await db_session.commit()
    assert (await client.get("/api/admin/me/", cookies=cookie)).status_code == 403


async def test_tampered_or_expired_sessions_are_refused(client, make_user, monkeypatch):
    admin = await make_user(role="admin", telegram_id=71007)
    good = admin_session.issue(user_id=str(admin.id), telegram_id=71007)
    assert (await client.get("/api/admin/me/", cookies={admin_session.COOKIE_NAME: good + "x"})).status_code == 401
    monkeypatch.setattr(admin_session, "MAX_AGE_S", -1)
    assert (await client.get("/api/admin/me/", cookies={admin_session.COOKIE_NAME: good})).status_code == 401


async def test_logout_clears_the_cookie(client):
    r = await client.post("/api/admin/auth/logout/")
    assert r.status_code == 200
    assert admin_session.COOKIE_NAME in r.headers.get("set-cookie", "")


async def test_config_names_the_bot_for_the_widget(client, monkeypatch):
    monkeypatch.setattr(settings, "telegram_bot_username", "loudrr_bot")
    assert (await client.get("/api/admin/auth/config/")).json() == {"bot_username": "loudrr_bot"}
