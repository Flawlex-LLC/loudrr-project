"""The admin website's session: a signed, expiring cookie.

The admin panel is a normal website (admin.loudrr.com), not part of the
Telegram mini-app. Admins sign in with the Telegram Login Widget; the backend
verifies that payload once and sets this cookie. Every admin request then
re-loads the user and re-checks the role (core/deps.py), so demoting or
banning someone ends their access on their next click, not at expiry.
"""
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.core.config import settings

COOKIE_NAME = "loudrr_admin"
MAX_AGE_S = 12 * 60 * 60
# mutating admin calls must carry this header: a cross-site form can't set a
# custom header, so a forged POST fails even if a browser ever sent the cookie
CSRF_HEADER = "x-requested-with"
CSRF_VALUE = "loudrr-admin"

_serializer = URLSafeTimedSerializer(settings.secret_key, salt="loudrr-admin-session")


def issue(*, user_id: str, telegram_id: int) -> str:
    return _serializer.dumps({"uid": user_id, "tg": telegram_id})


def read(token: str | None) -> dict | None:
    """The session payload, or None when missing, tampered with or expired."""
    if not token:
        return None
    try:
        data = _serializer.loads(token, max_age=MAX_AGE_S)
    except (BadSignature, SignatureExpired):
        return None
    return data if isinstance(data, dict) and data.get("uid") else None


def cookie_kwargs() -> dict:
    return {
        "key": COOKIE_NAME,
        "max_age": MAX_AGE_S,
        "httponly": True,            # scripts can't read it
        "secure": not settings.debug,  # HTTPS only in prod
        "samesite": "strict",        # never sent on cross-site requests
        "path": "/",
    }
