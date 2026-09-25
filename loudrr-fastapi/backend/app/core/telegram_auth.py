import hmac
import hashlib
import json
import time
from urllib.parse import parse_qsl


def verify_init_data(init_data: str, bot_token: str) -> dict:
    """Verify TG webapp init data, returns the TG user data as dict if valid,
    or raises ValueError if sign or expiry check fails."""
    # an empty bot token makes the HMAC key public — anyone could sign
    # initData for any user. Fail closed.
    if not bot_token:
        raise ValueError("Telegram bot token not configured")
    pairs = dict(parse_qsl(init_data, keep_blank_values=True))

    received_hash = pairs.pop("hash", None)
    if received_hash is None:
        raise ValueError("Missing hash in init data")

    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()

    check_hash = hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256
    ).hexdigest()

    # constant-time compare — never leak signature info via timing
    if not hmac.compare_digest(check_hash, received_hash):
        raise ValueError("Init data signature invalid")

    auth_date = int(pairs.get("auth_date", "0"))
    if time.time() - auth_date > 86400:
        raise ValueError("Init data expired")
 
    return json.loads(pairs["user"])


def verify_login_widget(data: dict, bot_token: str, *, max_age_s: int = 300) -> dict:
    """Verify a Telegram Login Widget payload (the website "Log in with
    Telegram" button — https://core.telegram.org/widgets/login).

    Unlike Mini App initData, the key is sha256(bot_token) and the fields
    arrive flat (id, first_name, username, photo_url, auth_date, hash).
    `max_age_s` is short on purpose: the payload is only exchanged once, for
    a session, so an old one captured anywhere can't be replayed.
    Returns the verified fields (without `hash`); raises ValueError otherwise.
    """
    if not bot_token:
        raise ValueError("Telegram bot token not configured")
    fields = {k: str(v) for k, v in (data or {}).items() if v is not None}
    received_hash = fields.pop("hash", None)
    if not received_hash:
        raise ValueError("Missing hash")
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret_key = hashlib.sha256(bot_token.encode()).digest()
    check_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(check_hash, received_hash):
        raise ValueError("Login signature invalid")
    try:
        auth_date = int(fields.get("auth_date", "0"))
    except ValueError:
        raise ValueError("Bad auth_date")
    age = time.time() - auth_date
    if age > max_age_s or age < -60:
        raise ValueError("Login expired")
    if not fields.get("id", "").lstrip("-").isdigit():
        raise ValueError("Missing Telegram id")
    return fields
