import json
from urllib.parse import parse_qsl

from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import settings

# One shared rate limiter, in its own module so both main.py and the route
# files can import it without a circular import (main imports the routers,
# the routers import the limiter — they must not import each other).
limiter = Limiter(key_func=get_remote_address)


def telegram_user_key(request) -> str:
    """Rate-limit key for AUTHENTICATED mini-app endpoints: the Telegram user,
    not the IP. Whole carrier-NAT ranges, VPNs and conference Wi-Fi share one
    IP, and behind a proxy hop every request can look like one address — a
    per-IP cap on the sign-up funnel would lock real users out.

    Reads the user id from the initData header WITHOUT verifying it: the
    endpoint's own auth dependency verifies the HMAC, so a forged id only ever
    rate-limits a request that is about to be rejected anyway. Falls back to
    the client IP when there's no identity to key on.
    """
    init_data = request.headers.get("x-telegram-init-data")
    if init_data:
        try:
            user = json.loads(dict(parse_qsl(init_data)).get("user") or "{}")
            return f"tg:{int(user['id'])}"
        except (ValueError, KeyError, TypeError):
            pass
    if settings.debug and request.query_params.get("telegram_id"):
        return f"tg:{request.query_params['telegram_id']}"
    return get_remote_address(request)
