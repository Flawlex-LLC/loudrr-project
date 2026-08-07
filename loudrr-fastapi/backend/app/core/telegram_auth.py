import hmac
import hashlib
import json
import time
from urllib.parse import parse_qsl


def verify_init_data(init_data: str, bot_token: str) -> dict:
    """Verify TG webapp init data, returns the TG user data as dict if valid,
    or raises ValueError if sign or expiry check fails."""
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
