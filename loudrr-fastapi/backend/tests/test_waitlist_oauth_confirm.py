"""Browser confirmation of the waitlist X OAuth link (2026-09-17).

The hole: someone presses Connect X in their OWN mini-app, copies the X
authorize URL (its state is bound to their telegram_id) and sends it to a
victim. The victim authorizes on X, the callback mints a proof binding the
VICTIM's X account to the sender's Telegram, and the sender's poll picks it up
and joins the waitlist as the victim (score, tier, handle).

Now the callback stores the proof UNCONFIRMED and sends the browser that just
authorized to /waitlist/oauth-return?confirm=<one-time token>, which names the
Telegram account that started the flow. Nothing is released until that browser
confirms. Each test pins one piece:

  * callback: unconfirmed row, hashed token, confirm token in the redirect
  * poll: awaiting_confirmation, the handle withheld; "cancelled" reported once
  * POST /waitlist/x-oauth/confirm/info/ and /confirm/: info, confirm, cancel,
    single use, expiry, unknown tokens, concurrent decisions
  * register refuses any proof that isn't confirmed, including one that
    leaked before confirmation, whatever happened to its row afterwards
  * who-started-it label from real initData, sanitized
  * the migration renders both ways
"""
import asyncio
import hashlib
import hmac
import json
import os
import subprocess
import sys
import time
from datetime import timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.core.crypto import verify_x_proof
from app.core.errors import NotFound
from app.core.time_utils import utcnow
from app.integrations import x_oauth
from app.models.waitlist_entry import WaitlistEntry
from app.models.waitlist_oauth_proof import WaitlistOAuthProof
from app.models.waitlist_oauth_state import WaitlistOAuthState
from app.services import waitlist_x_oauth as oauth_svc

BACKEND_DIR = Path(__file__).resolve().parents[1]
# the database the db_session fixture built (conftest honors TEST_DATABASE_NAME)
TEST_DATABASE_URL = (
    settings.database_url.rsplit("/", 1)[0] + "/" + os.environ.get("TEST_DATABASE_NAME", "loudrr_test")
)
BOT_TOKEN = "123456:confirm-flow-test-token"

EMPTY_POLL = {
    "proof": None, "x_username": None, "expires_in": None, "error": None,
    "awaiting_confirmation": False,
}
AWAITING = EMPTY_POLL | {"awaiting_confirmation": True}
GONE = oauth_svc.CONFIRM_LINK_GONE

ATTACKER = {"id": 7_100_001, "username": "mallory", "first_name": "Mallory"}
VICTIM_X = ("victim_whale", "990001")


@pytest.fixture(autouse=True)
def x_configured(monkeypatch):
    """Waitlist OAuth configured, real Telegram initData checks available, and
    X's two HTTP calls faked: code "code:<handle>:<id>" authorizes that account
    (same trick as the live e2e launcher)."""
    monkeypatch.setattr(settings, "x_oauth_client_id", "cid")
    monkeypatch.setattr(settings, "x_oauth_client_secret", "sec")
    monkeypatch.setattr(settings, "x_oauth_callback_url", "https://api.example.com/api/auth/x/callback/")
    monkeypatch.setattr(
        settings, "x_oauth_waitlist_callback_url", "https://api.example.com/api/auth/x/callback/waitlist/",
    )
    monkeypatch.setattr(settings, "miniapp_url", "https://app.example.com/app")
    monkeypatch.setattr(settings, "telegram_bot_token", BOT_TOKEN)

    async def exchange(code, code_verifier, *, redirect_uri=None):
        assert code_verifier and redirect_uri == settings.x_oauth_waitlist_callback_url
        return "tok:" + code.split(":", 1)[1] if code.startswith("code:") else None

    async def fetch_me(token):
        handle, x_id = token[4:].split(":")
        return {"id": x_id, "username": handle, "name": handle}

    monkeypatch.setattr(x_oauth, "exchange_code_for_token", exchange)
    monkeypatch.setattr(x_oauth, "fetch_me", fetch_me)


# ---- helpers: the mini-app (Telegram) side and the browser side ----
def _init_data(user: dict) -> dict:
    """X-Telegram-Init-Data header signed with the test bot token."""
    pairs = {"auth_date": str(int(time.time())), "user": json.dumps(user)}
    dcs = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    pairs["hash"] = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    return {"X-Telegram-Init-Data": urlencode(pairs)}


def _as(tg: int | dict) -> dict:
    """Request kwargs authenticating as a Telegram user: a dict is a real
    initData user, an int uses the debug ?telegram_id= bypass."""
    return {"headers": _init_data(tg)} if isinstance(tg, dict) else {"params": {"telegram_id": tg}}


async def _start(client, tg) -> str:
    """Press Connect X; returns the state inside the authorize URL."""
    r = await client.post("/waitlist/x-oauth/start/", **_as(tg))
    assert r.status_code == 200, r.text
    return parse_qs(urlparse(r.json()["authorize_url"]).query)["state"][0]


async def _authorize(client, state, handle, x_id) -> str:
    """Whoever holds the authorize URL approves on X, and X redirects their
    browser to the callback. Returns the confirm token from the 302."""
    r = await client.get(
        "/api/auth/x/callback/waitlist/",
        params={"code": f"code:{handle}:{x_id}", "state": state},
        follow_redirects=False,
    )
    assert r.status_code == 302, r.text
    location = urlparse(r.headers["location"])
    assert location.path == "/waitlist/oauth-return"
    # the token rides the FRAGMENT: browsers never send it to a server, so it
    # can't land in the frontend's access log, a Referer header or an analytics
    # tag. Nothing else may ride along with it.
    assert not location.query, r.headers["location"]
    fragment = parse_qs(location.fragment)
    assert list(fragment) == ["confirm"], r.headers["location"]
    return fragment["confirm"][0]


async def _poll(client, tg) -> dict:
    r = await client.get("/waitlist/x-oauth/proof/", **_as(tg))
    assert r.status_code == 200, r.text
    return r.json()


async def _decide(client, token, decision):
    return await client.post("/waitlist/x-oauth/confirm/", json={"token": token, "decision": decision})


async def _register(client, tg, proof):
    return await client.post("/waitlist/register/", json={"x_proof": proof}, **_as(tg))


async def _stored_row(db_session, tg_id) -> WaitlistOAuthProof | None:
    return (
        await db_session.execute(
            select(WaitlistOAuthProof)
            .where(WaitlistOAuthProof.telegram_id == tg_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()


async def _entries(db_session) -> int:
    return (await db_session.execute(select(func.count()).select_from(WaitlistEntry))).scalar_one()


# ---------------------------------------------------------------------------
# callback + poll
# ---------------------------------------------------------------------------
async def test_callback_stores_an_unconfirmed_proof_and_redirects_with_a_confirm_token(client, db_session):
    token = await _authorize(client, await _start(client, 5001), "alice", "111")

    row = await _stored_row(db_session, 5001)
    assert verify_x_proof(row.proof)["x_username"] == "alice"
    assert row.error is None
    assert row.confirmed_at is None
    # only the hash is stored, and neither the token nor the proof is in the other
    assert row.confirm_token_hash == hashlib.sha256(token.encode()).hexdigest()
    assert token not in row.proof and row.proof not in token
    assert row.telegram_label == "@Oxblest (Oxblest)"   # the debug identity
    assert len(token) >= 43                              # token_urlsafe(32)


async def test_poll_withholds_the_unconfirmed_proof_and_even_the_handle(client):
    await _authorize(client, await _start(client, 5002), "alice", "112")
    assert await _poll(client, 5002) == AWAITING
    # non-destructive while waiting
    assert await _poll(client, 5002) == AWAITING


async def test_confirm_info_shows_the_x_handle_and_the_telegram_account(client):
    token = await _authorize(client, await _start(client, ATTACKER), *VICTIM_X)
    r = await client.post("/waitlist/x-oauth/confirm/info/", json={"token": token})
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"x_username", "telegram_label", "expires_in"}
    assert body["x_username"] == "victim_whale"
    assert body["telegram_label"] == "@mallory (Mallory)"
    assert 590 <= body["expires_in"] <= 600
    assert r.headers["cache-control"] == "no-store"
    # read-only: looking doesn't use the token up
    assert (await client.post("/waitlist/x-oauth/confirm/info/", json={"token": token})).json()["x_username"] == "victim_whale"


# ---------------------------------------------------------------------------
# confirm / cancel
# ---------------------------------------------------------------------------
async def test_confirm_releases_the_proof_to_the_poll_and_register_works(client, db_session):
    token = await _authorize(client, await _start(client, 5003), "carol", "113")

    r = await _decide(client, token, "confirm")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "status": "confirmed"}
    row = await _stored_row(db_session, 5003)
    assert row.confirmed_at is not None and row.confirm_token_hash is None

    polled = await _poll(client, 5003)
    assert polled["x_username"] == "carol"
    assert polled["awaiting_confirmation"] is False and polled["error"] is None
    assert verify_x_proof(polled["proof"])["tg_id"] == 5003
    assert 590 <= polled["expires_in"] <= 600   # still counted from the callback

    reg = await _register(client, 5003, polled["proof"])
    assert reg.status_code == 200, reg.text
    assert reg.json()["status"] == "registered" and reg.json()["x_username"] == "carol"
    assert await _poll(client, 5003) == EMPTY_POLL   # handoff row gone


async def test_cancel_reports_cancelled_once_and_nothing_is_connected(client, db_session):
    token = await _authorize(client, await _start(client, 5004), "dave", "114")

    r = await _decide(client, token, "cancel")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "status": "cancelled"}
    assert (await _stored_row(db_session, 5004)).proof == ""

    assert await _poll(client, 5004) == EMPTY_POLL | {"error": "cancelled"}
    assert await _poll(client, 5004) == EMPTY_POLL      # reported once
    assert await _stored_row(db_session, 5004) is None
    assert (await client.post("/waitlist/x-oauth/confirm/info/", json={"token": token})).status_code == 404


async def test_the_confirm_token_is_single_use(client):
    token = await _authorize(client, await _start(client, 5005), "erin", "115")
    assert (await _decide(client, token, "confirm")).status_code == 200

    for decision in ("confirm", "cancel"):
        again = await _decide(client, token, decision)
        assert again.status_code == 404
        assert again.json() == {"error": GONE}
    assert (await client.post("/waitlist/x-oauth/confirm/info/", json={"token": token})).status_code == 404
    # the late cancel changed nothing
    assert (await _poll(client, 5005))["x_username"] == "erin"


async def test_an_expired_confirm_token_is_404(client, db_session):
    token = await _authorize(client, await _start(client, 5006), "frank", "116")
    await db_session.execute(
        update(WaitlistOAuthProof)
        .where(WaitlistOAuthProof.telegram_id == 5006)
        .values(created_at=utcnow() - timedelta(seconds=601))
    )
    await db_session.commit()

    info = await client.post("/waitlist/x-oauth/confirm/info/", json={"token": token})
    assert info.status_code == 404 and info.json() == {"error": GONE}
    for decision in ("confirm", "cancel"):
        assert (await _decide(client, token, decision)).status_code == 404
    assert await _poll(client, 5006) == EMPTY_POLL | {"error": "expired"}


async def test_unknown_tokens_get_the_same_404(client):
    await _authorize(client, await _start(client, 5007), "grace", "117")
    for token in ("x" * 43, "nope", "A" * 128):
        info = await client.post("/waitlist/x-oauth/confirm/info/", json={"token": token})
        assert info.status_code == 404 and info.json() == {"error": GONE}
        post = await _decide(client, token, "confirm")
        assert post.status_code == 404 and post.json() == {"error": GONE}
    assert await _poll(client, 5007) == AWAITING   # the real one is untouched


async def test_a_decision_must_be_confirm_or_cancel(client):
    token = await _authorize(client, await _start(client, 5008), "heidi", "118")
    assert (await _decide(client, token, "yes")).status_code == 422
    assert (await client.post("/waitlist/x-oauth/confirm/", json={"token": token})).status_code == 422
    assert await _poll(client, 5008) == AWAITING


async def test_a_newer_attempt_supersedes_the_older_confirm_link(client):
    old = await _authorize(client, await _start(client, 5009), "ivan", "119")
    new = await _authorize(client, await _start(client, 5009), "ivan", "119")
    assert (await _decide(client, old, "confirm")).status_code == 404
    assert (await _decide(client, new, "confirm")).status_code == 200
    assert (await _poll(client, 5009))["x_username"] == "ivan"


@pytest.mark.parametrize("first, second", [
    ("confirm", "cancel"), ("cancel", "confirm"), ("confirm", "confirm"), ("cancel", "cancel"),
])
async def test_concurrent_decisions_change_the_row_exactly_once(client, db_session, first, second):
    """Two decisions for one token on separate connections, released at the
    same instant: both statements queue behind a row lock held by a third
    transaction, so Postgres must re-check the WHERE for whichever runs
    second. Exactly one wins, the other gets the 404."""
    token = await _authorize(client, await _start(client, 5010), "judy", "120")
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async def decide(decision):
        async with Session() as s:
            try:
                return (await oauth_svc.decide_confirmation(s, token=token, decision=decision))["status"]
            except NotFound:
                return "404"

    async def blocked_on_locks() -> int:
        async with engine.connect() as conn:
            return (await conn.execute(text(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE datname = current_database() AND wait_event_type = 'Lock'"
            ))).scalar_one()

    try:
        async with Session() as locker:
            await locker.execute(
                select(WaitlistOAuthProof.telegram_id)
                .where(WaitlistOAuthProof.telegram_id == 5010)
                .with_for_update()
            )
            tasks = [asyncio.create_task(decide(first)), asyncio.create_task(decide(second))]
            for _ in range(200):
                if await blocked_on_locks() >= 2:
                    break
                await asyncio.sleep(0.025)
            else:
                pytest.fail("the two decisions never queued behind the row lock")
            await locker.commit()   # release: both proceed at once
        results = await asyncio.gather(*tasks)
    finally:
        await engine.dispose()

    winners = [r for r in results if r != "404"]
    assert len(winners) == 1, results
    polled = await _poll(client, 5010)
    if winners[0] == "confirmed":
        assert polled["x_username"] == "judy" and polled["error"] is None
    else:
        assert polled == EMPTY_POLL | {"error": "cancelled"}


# ---------------------------------------------------------------------------
# register refuses anything unconfirmed
# ---------------------------------------------------------------------------
async def test_register_refuses_an_unconfirmed_proof(client, db_session):
    await _authorize(client, await _start(client, 5011), "ken", "121")
    leaked = (await _stored_row(db_session, 5011)).proof   # e.g. read out of a backup

    r = await _register(client, 5011, leaked)
    assert r.status_code == 400
    assert "not confirmed" in r.json()["error"]
    assert "OAuth proof" in r.json()["error"]   # the mini-app's "connect X again" path
    assert await _entries(db_session) == 0
    assert await _poll(client, 5011) == AWAITING   # the pending link still works


async def test_a_legacy_row_without_a_confirm_token_is_dead(client, db_session):
    """Rows stored before this migration have no token hash and no
    confirmed_at. Nothing can confirm them: poll says expired, register refuses."""
    from app.core.crypto import sign_x_proof
    proof = sign_x_proof({"tg_id": 5012, "x_username": "legacy", "x_user_id": "122", "iat": 0})
    db_session.add(WaitlistOAuthProof(telegram_id=5012, proof=proof))
    await db_session.commit()

    assert (await _register(client, 5012, proof)).status_code == 400
    assert await _poll(client, 5012) == EMPTY_POLL | {"error": "expired"}
    assert await _poll(client, 5012) == EMPTY_POLL


async def test_forwarded_authorize_link_cannot_register_the_victim(client, db_session):
    """The reported attack, end to end, including the ways the pending row can
    disappear after the victim's proof leaked."""
    state = await _start(client, ATTACKER)              # attacker presses Connect X
    token = await _authorize(client, state, *VICTIM_X)  # ...and forwards the URL; the victim approves

    # the attacker's mini-app learns nothing, not even whose account it was
    assert await _poll(client, ATTACKER) == AWAITING
    leaked = (await _stored_row(db_session, ATTACKER["id"])).proof
    assert (await _register(client, ATTACKER, leaked)).status_code == 400

    # the victim's browser names the account they'd be handing their X to
    info = (await client.post("/waitlist/x-oauth/confirm/info/", json={"token": token})).json()
    assert (info["x_username"], info["telegram_label"]) == ("victim_whale", "@mallory (Mallory)")
    assert (await _decide(client, token, "cancel")).json()["status"] == "cancelled"

    assert (await _register(client, ATTACKER, leaked)).status_code == 400   # the "cancelled" row
    assert await _poll(client, ATTACKER) == EMPTY_POLL | {"error": "cancelled"}
    assert await _stored_row(db_session, ATTACKER["id"]) is None
    assert (await _register(client, ATTACKER, leaked)).status_code == 400   # no row at all

    # the attacker connects (and confirms) their OWN X account: the leaked
    # proof still doesn't match the confirmed row
    own = await _authorize(client, await _start(client, ATTACKER), "mallory_x", "990002")
    assert (await _decide(client, own, "confirm")).status_code == 200
    r = await _register(client, ATTACKER, leaked)
    assert r.status_code == 400 and "not confirmed" in r.json()["error"]
    assert await _entries(db_session) == 0

    # ...and registering with their own confirmed proof gets their own handle
    mine = (await _poll(client, ATTACKER))["proof"]
    ok = await _register(client, ATTACKER, mine)
    assert ok.status_code == 200 and ok.json()["x_username"] == "mallory_x"


# ---------------------------------------------------------------------------
# who started the flow
# ---------------------------------------------------------------------------
async def test_start_stores_the_label_from_verified_init_data(client, db_session):
    user = {"id": 5013, "username": "alice_tg", "first_name": "Alice", "last_name": "B"}
    state = await _start(client, user)
    row = (await db_session.execute(
        select(WaitlistOAuthState).where(WaitlistOAuthState.state == state)
    )).scalar_one()
    assert row.telegram_id == 5013
    assert row.telegram_label == "@alice_tg (Alice B)"

    token = await _authorize(client, state, "alice_x", "123")
    assert (await _stored_row(db_session, 5013)).telegram_label == "@alice_tg (Alice B)"
    info = (await client.post("/waitlist/x-oauth/confirm/info/", json={"token": token})).json()
    assert info["telegram_label"] == "@alice_tg (Alice B)"


def test_telegram_label_formats_and_cannot_be_spoofed():
    describe = oauth_svc.describe_telegram_user
    assert describe({"id": 1, "username": "alice", "first_name": "Alice", "last_name": "B"}) == "@alice (Alice B)"
    assert describe({"id": 1, "username": "alice"}) == "@alice"
    assert describe({"id": 1, "first_name": "Alice"}) == "Alice"
    assert describe({"id": 42}) == "Telegram user 42"
    assert describe({"id": 42, "first_name": "  ", "last_name": "​"}) == "Telegram user 42"

    # a display name can't pose as a verified @username or fake the brackets,
    # fullwidth lookalikes included
    assert describe({"id": 1, "first_name": "@victim (Victim)"}) == "victim Victim"
    assert describe({"id": 1, "first_name": "＠victim （V）"}) == "victim V"
    # bidi overrides / control characters can't reorder or break the text
    assert describe({"id": 1, "username": "eve", "first_name": "‮evil‬\nname"}) == "@eve (evil name)"
    # the username part is only ever username characters
    assert describe({"id": 1, "username": "a b(c)@d"}) == "@abcd"

    long_label = describe({"id": 1, "username": "u" * 40, "first_name": "N" * 300})
    assert len(long_label) <= 120 and long_label.startswith("@" + "u" * 32 + " (")


# ---------------------------------------------------------------------------
# migration
# ---------------------------------------------------------------------------
REVISION = "1b8993ddd938"
DOWN_REVISION = "d5f7a9b1c3e6"


def _alembic_sql(*args: str) -> str:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args, "--sql"],
        cwd=BACKEND_DIR, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=120,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    return result.stdout


def test_migration_renders_upgrade_and_downgrade_sql():
    up = _alembic_sql("upgrade", f"{DOWN_REVISION}:{REVISION}")
    for ddl in (
        "ALTER TABLE waitlist_oauth_states ADD COLUMN telegram_label VARCHAR(120) DEFAULT '' NOT NULL",
        "ALTER TABLE waitlist_oauth_proofs ADD COLUMN telegram_label VARCHAR(120) DEFAULT '' NOT NULL",
        "ALTER TABLE waitlist_oauth_proofs ADD COLUMN confirm_token_hash VARCHAR(64)",
        "ALTER TABLE waitlist_oauth_proofs ADD COLUMN confirmed_at TIMESTAMP WITHOUT TIME ZONE",
        "CREATE UNIQUE INDEX ix_waitlist_oauth_proofs_confirm_token_hash "
        "ON waitlist_oauth_proofs (confirm_token_hash)",
        f"UPDATE alembic_version SET version_num='{REVISION}' "
        f"WHERE alembic_version.version_num = '{DOWN_REVISION}'",
    ):
        assert ddl in up, ddl

    down = _alembic_sql("downgrade", f"{REVISION}:{DOWN_REVISION}")
    for ddl in (
        "DELETE FROM waitlist_oauth_proofs WHERE confirmed_at IS NULL AND error IS NULL",
        "DROP INDEX ix_waitlist_oauth_proofs_confirm_token_hash",
        "ALTER TABLE waitlist_oauth_proofs DROP COLUMN confirmed_at",
        "ALTER TABLE waitlist_oauth_proofs DROP COLUMN confirm_token_hash",
        "ALTER TABLE waitlist_oauth_proofs DROP COLUMN telegram_label",
        "ALTER TABLE waitlist_oauth_states DROP COLUMN telegram_label",
        f"UPDATE alembic_version SET version_num='{DOWN_REVISION}' "
        f"WHERE alembic_version.version_num = '{REVISION}'",
    ):
        assert ddl in down, ddl
    # pending proofs are dropped BEFORE the columns that identify them go
    assert down.index("DELETE FROM waitlist_oauth_proofs") < down.index("DROP COLUMN confirmed_at")
