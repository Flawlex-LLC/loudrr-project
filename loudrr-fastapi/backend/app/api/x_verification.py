import logging

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse

from app.core.deps import get_current_user
from app.db.session import get_session
from app.models.user import User
from app.services import x_verification as svc

logger = logging.getLogger(__name__)
router = APIRouter(tags=["x-verification"])


# ---- endpoint 5 ----
@router.post("/x-oauth/start/")
async def x_oauth_start(
    user: User = Depends(get_current_user),
    db=Depends(get_session),
):
    url = await svc.start_oauth(db, user=user)
    return {"authorize_url": url}


# ---- endpoint 6 ----
@router.post("/x-verification/confirm-mismatch/")
async def confirm_mismatch(
    user: User = Depends(get_current_user),
    db=Depends(get_session),
):
    return await svc.confirm_mismatch(db, user=user)


# ---- endpoint 7 ----
@router.post("/x-verification/cancel-mismatch/")
async def cancel_mismatch(
    user: User = Depends(get_current_user),
    db=Depends(get_session),
):
    return await svc.cancel_mismatch(db, user=user)


# ---- public OAuth callback (X redirects the browser here) ----
def _callback_html(title: str, message: str, success: bool = True) -> str:
    color = "#22c55e" if success else "#ef4444"
    accent = "#f95400"
    mark = "✓" if success else "!"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>{title} — Loudrr</title>
<style>
  *{{box-sizing:border-box}}
  body{{margin:0;background:#08080a;color:#fff;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
       min-height:100vh;display:flex;align-items:center;justify-content:center;padding:24px;}}
  .card{{max-width:420px;width:100%;background:linear-gradient(180deg,#0e0e10,#08080a);
        border:1px solid rgba(255,255,255,0.1);border-radius:24px;padding:40px 28px;text-align:center;}}
  .badge{{width:64px;height:64px;border-radius:50%;background:{color}22;color:{color};
         display:inline-flex;align-items:center;justify-content:center;font-size:32px;margin-bottom:18px;font-weight:700;}}
  h1{{margin:0 0 10px;font-size:22px;letter-spacing:-0.5px;}}
  p{{margin:0;color:rgba(255,255,255,0.7);font-size:15px;line-height:1.5;}}
  .brand{{margin-top:22px;font-weight:700;color:{accent};letter-spacing:1px;font-size:14px;}}
</style></head>
<body><div class="card">
  <div class="badge">{mark}</div>
  <h1>{title}</h1>
  <p>{message}</p>
  <div class="brand">Return to Loudrr in Telegram</div>
</div></body></html>"""


@router.get("/api/auth/x/callback/")
async def x_oauth_callback(
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    db=Depends(get_session),
):
    # public, unauthenticated endpoint (X redirects the browser here) — it must
    # never show a raw 500 stack page on unexpected input/infra errors
    try:
        result = await svc.handle_callback(db, code=code, state=state, error=error)
    except Exception:
        logger.exception("x oauth callback failed")
        result = svc.CallbackResult(
            "Something Went Wrong",
            "We couldn't complete verification. Open Loudrr again to retry.",
            False, 500,
        )
    return HTMLResponse(
        content=_callback_html(result.title, result.message, result.success),
        status_code=result.status_code,
    )
