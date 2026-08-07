"""Security: the real Telegram HMAC path (not the debug bypass).

Proves verify_init_data accepts a correctly-signed payload and rejects a
forged signature, an expired auth_date, and a missing hash.
"""
import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest

from app.core.telegram_auth import verify_init_data

BOT_TOKEN = "test-bot-token-123:ABC"


def _sign(bot_token: str, user: dict, *, auth_date: int | None = None) -> str:
    auth_date = auth_date if auth_date is not None else int(time.time())
    pairs = {"auth_date": str(auth_date), "user": json.dumps(user)}
    dcs = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    pairs["hash"] = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    return urlencode(pairs)


def test_valid_init_data_passes():
    init = _sign(BOT_TOKEN, {"id": 42, "username": "zoe"})
    user = verify_init_data(init, BOT_TOKEN)
    assert user["id"] == 42 and user["username"] == "zoe"


def test_tampered_payload_rejected():
    init = _sign(BOT_TOKEN, {"id": 42})
    # flip the user id after signing → signature no longer matches
    tampered = init.replace("%22id%22%3A+42", "%22id%22%3A+999")
    if tampered == init:  # encoding differed; force a mismatch another way
        tampered = init + "x"
    with pytest.raises(ValueError):
        verify_init_data(tampered, BOT_TOKEN)


def test_wrong_bot_token_rejected():
    init = _sign(BOT_TOKEN, {"id": 42})
    with pytest.raises(ValueError):
        verify_init_data(init, "a-different-token")


def test_expired_init_data_rejected():
    init = _sign(BOT_TOKEN, {"id": 42}, auth_date=int(time.time()) - 90000)  # >24h
    with pytest.raises(ValueError):
        verify_init_data(init, BOT_TOKEN)


def test_missing_hash_rejected():
    with pytest.raises(ValueError):
        verify_init_data("auth_date=123&user=%7B%7D", BOT_TOKEN)
