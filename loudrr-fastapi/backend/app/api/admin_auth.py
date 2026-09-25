"""Admin website sign-in — Telegram Login Widget → session cookie.

  GET  /api/admin/auth/config/    public: the bot username the widget needs
  POST /api/admin/auth/telegram/  the widget's signed payload → session cookie
  POST /api/admin/auth/logout/    clears it

Signing in requires an existing Loudrr account with the admin or superadmin
role. Anyone else gets a 403 and no cookie.
"""
import logging

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel
from sqlalchemy import select

from app.core import admin_session
from app.core.config import settings
from app.core.errors import Forbidden, Unauthorized
from app.core.limiter import limiter
from app.core.telegram_auth import verify_login_widget
from app.db.session import get_session
from app.models.audit_log import AuditLog
from app.models.user import User

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin/auth", tags=["admin-auth"])


class TelegramLoginPayload(BaseModel):
    # exactly the fields the widget signs; extra keys are rejected by the
    # hash check anyway, so accept and pass everything through
    model_config = {"extra": "allow"}
    id: int
    auth_date: int
    hash: str


@router.get("/config/")
async def auth_config():
    return {"bot_username": settings.telegram_bot_username}


@router.post("/telegram/")
@limiter.limit("20/minute")
async def telegram_login(
    request: Request,
    response: Response,
    body: TelegramLoginPayload,
    db=Depends(get_session),
):
    try:
        fields = verify_login_widget(body.model_dump(), settings.telegram_bot_token)
    except ValueError as e:
        logger.warning("admin login rejected: %s", e)
        raise Unauthorized("Telegram sign-in failed — try again")

    user = await db.scalar(select(User).where(User.telegram_id == int(fields["id"])))
    if user is None or user.role not in ("admin", "superadmin") or user.is_banned:
        who = fields.get("username") or fields["id"]
        logger.warning("admin login refused for telegram %s", who)
        raise Forbidden("This Telegram account isn't a Loudrr admin")

    db.add(AuditLog(actor_id=user.id, action="admin_login", target_type="user",
                    target_id=user.id, detail={"via": "telegram_login_widget"}))
    await db.commit()
    response.set_cookie(
        value=admin_session.issue(user_id=str(user.id), telegram_id=user.telegram_id),
        **admin_session.cookie_kwargs(),
    )
    return {
        "ok": True,
        "id": str(user.id),
        "telegram_username": user.telegram_username or "",
        "role": user.role,
    }


@router.post("/logout/")
async def logout(response: Response):
    kwargs = admin_session.cookie_kwargs()
    response.delete_cookie(
        key=kwargs["key"], path=kwargs["path"], secure=kwargs["secure"],
        httponly=True, samesite="strict",
    )
    return {"ok": True}
