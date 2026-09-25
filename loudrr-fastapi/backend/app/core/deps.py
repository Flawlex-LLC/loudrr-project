import uuid

from fastapi import Header, HTTPException, Depends, Query, Request
from sqlalchemy import select
from app.core.telegram_auth import verify_init_data
from app.core.config import settings
from app.core.errors import Forbidden
from app.models.user import User
from app.db.session import get_session

async def get_current_user(
    x_telegram_init_data: str | None = Header(default=None),
    db=Depends(get_session),
    telegram_id: int | None = Query(default=None),
) -> User:
    # DEV BYPASS ONLY! REMOVE THIS IN PRODUCTION!
    if settings.debug and telegram_id is not None:
        tg_id = telegram_id
    else:
        # checks if init data is there; if not, 401
        if x_telegram_init_data is None:
            raise HTTPException(status_code=401, detail="missing init data")
        try:
            tg_user = verify_init_data(
                x_telegram_init_data, settings.telegram_bot_token
            )
        except ValueError:
            raise HTTPException(status_code=401, detail="invalid init data")
        tg_id = tg_user["id"]

    # look up the user by telegram_id
    result = await db.execute(select(User).where(User.telegram_id == tg_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=401, detail="user not found")
    return user


# ---- the admin website's identity ----
async def get_admin_user(
    request: Request,
    db=Depends(get_session),
    telegram_id: int | None = Query(default=None),
) -> User:
    """Who is using the admin website.

    The admin panel is a normal website, not part of the Telegram mini-app:
    identity is the session cookie set by the Telegram Login Widget
    (api/admin_auth.py). Mini-app initData does NOT open admin routes — inside
    Telegram an admin is just a user.
    """
    from app.core import admin_session

    if settings.debug and telegram_id is not None:  # dev/test bypass only
        user = await db.scalar(select(User).where(User.telegram_id == telegram_id))
        if user is None:
            raise HTTPException(status_code=401, detail="user not found")
        return user

    session = admin_session.read(request.cookies.get(admin_session.COOKIE_NAME))
    if session is None:
        raise HTTPException(status_code=401, detail="Sign in to the admin panel")
    if request.method not in ("GET", "HEAD", "OPTIONS") and (
        request.headers.get(admin_session.CSRF_HEADER) != admin_session.CSRF_VALUE
    ):
        raise HTTPException(status_code=403, detail="Missing admin request header")
    try:
        user = await db.get(User, uuid.UUID(str(session["uid"])))
    except ValueError:
        user = None
    if user is None or user.telegram_id != session.get("tg") or user.is_banned:
        raise HTTPException(status_code=401, detail="Sign in to the admin panel")
    return user


# ---- RBAC: admin gates (build on the admin identity + their role) ----
async def require_admin(user: User = Depends(get_admin_user)) -> User:
    """Signed-in admin website user with the 'admin' or 'superadmin' role
    (Forbidden→403). The role is re-read on every request, so a demotion takes
    effect on the next click."""
    if user.role not in ("admin", "superadmin"):
        raise Forbidden("Admin access required")
    return user


async def require_superadmin(user: User = Depends(get_admin_user)) -> User:
    """Authenticated user with the 'superadmin' role — for the most sensitive
    operations (e.g. revoking credits), matching the Django superuser gate."""
    if user.role != "superadmin":
        raise Forbidden("Superadmin access required")
    return user


async def get_telegram_identity(
    x_telegram_init_data: str | None = Header(default=None),
    telegram_id: int | None = Query(default=None),
) -> dict:
    """Verified Telegram user dict, for endpoints where the caller is
    authenticated but may NOT have a User row yet (waitlist,
    X-verification callback)."""
    from app.core.errors import Unauthorized
    # DEV SHORTCUT: when debug=True, trust ?telegram_id= so you can test
    # in a browser without a real signed Telegram request. NEVER in prod.
    # The mocked identity matches Django's reference (miniapp/views.py:144)
    # so a debug waitlist registration lands as "Oxblest" instead of blank.
    if settings.debug and telegram_id is not None:
        return {"id": telegram_id, "username": "Oxblest", "first_name": "Oxblest"}
    if not x_telegram_init_data:
        raise Unauthorized("Invalid Telegram data")
    try:
        # verify_init_data (built in Ch6) checks the HMAC signature with
        # your bot token. Valid -> user dict; bad -> ValueError.
        return verify_init_data(
            x_telegram_init_data, settings.telegram_bot_token
        )
    except ValueError:
        raise Unauthorized("Invalid Telegram data")