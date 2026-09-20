import html
import logging
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.core.deps import get_current_user
from app.core.limiter import limiter
from app.db.session import get_session
from app.models.user import User
from app.services import waitlist_x_oauth as waitlist_oauth_svc
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
# These pages run in the system browser, outside Telegram. They are never
# cached, never send a Referer, and refuse to be framed (the confirm button must
# not be clickjackable).
_PAGE_HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "frame-ancestors 'none'",
}


def _callback_html(title: str, message: str, success: bool = True) -> str:
    color = "#22c55e" if success else "#ef4444"
    accent = "#f95400"
    mark = "✓" if success else "!"
    title = html.escape(title)
    message = html.escape(message)
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


def _confirm_html(result) -> str:
    """The page that names both accounts and asks the person who just
    authorized on X to confirm. The one-time token lives only in this form's
    hidden field — never in a URL, a log line or a Referer."""
    x_handle = html.escape(result.x_username)
    telegram = html.escape(result.telegram_label)
    token = html.escape(result.confirm_token or "", quote=True)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>Confirm this connection — Loudrr</title>
<style>
  *{{box-sizing:border-box}}
  body{{margin:0;background:#08080a;color:#fff;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
       min-height:100vh;display:flex;align-items:center;justify-content:center;padding:24px;}}
  .card{{max-width:420px;width:100%;background:linear-gradient(180deg,#0e0e10,#08080a);
        border:1px solid rgba(255,255,255,0.1);border-radius:24px;padding:32px 24px;}}
  h1{{margin:0 0 8px;font-size:22px;letter-spacing:-0.5px;text-align:center;}}
  .sub{{margin:0 0 20px;color:rgba(255,255,255,0.65);font-size:14px;text-align:center;}}
  .box{{background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.08);border-radius:16px;padding:16px;margin-bottom:16px;}}
  .k{{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:rgba(255,255,255,0.45);}}
  .v{{font-size:15px;font-weight:600;word-break:break-word;margin-top:2px;}}
  .to{{text-align:center;font-size:12px;color:rgba(255,255,255,0.4);margin:12px 0;}}
  .warn{{font-size:13px;color:rgba(255,255,255,0.6);line-height:1.5;margin:0 0 20px;}}
  button{{width:100%;height:48px;border-radius:16px;font-size:15px;font-weight:600;cursor:pointer;}}
  .yes{{background:rgba(249,84,0,0.22);border:1px solid rgba(249,84,0,0.5);color:#fff;margin-bottom:10px;}}
  .no{{background:transparent;border:1px solid rgba(255,255,255,0.16);color:rgba(255,255,255,0.8);}}
</style></head>
<body><div class="card">
  <h1>Confirm this connection</h1>
  <p class="sub">You just authorized Loudrr on X. Check both accounts first.</p>
  <div class="box">
    <div class="k">X account</div><div class="v">@{x_handle}</div>
    <div class="to">will be connected to</div>
    <div class="k">Telegram account</div><div class="v">{telegram}</div>
  </div>
  <p class="warn">Only continue if that Telegram account is yours. If someone sent you this link,
  press Cancel — confirming would hand <b>@{x_handle}</b> to their Loudrr account.</p>
  <form method="post" action="/api/auth/x/confirm/">
    <input type="hidden" name="token" value="{token}" />
    <button class="yes" type="submit" name="decision" value="confirm">Yes, connect @{x_handle}</button>
    <button class="no" type="submit" name="decision" value="cancel">Cancel — this wasn't me</button>
  </form>
</div></body></html>"""


@router.get("/api/auth/x/callback/")
@limiter.limit("30/minute")  # public unauthenticated endpoint — cap abuse
async def x_oauth_callback(
    request: Request,                                # slowapi needs the IP
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
    if result.confirm_token:
        return HTMLResponse(content=_confirm_html(result), status_code=200, headers=_PAGE_HEADERS)
    return HTMLResponse(
        content=_callback_html(result.title, result.message, result.success),
        status_code=result.status_code, headers=_PAGE_HEADERS,
    )


@router.post("/api/auth/x/confirm/")
@limiter.limit("60/minute")  # public: the one-time token is the only credential
async def x_oauth_confirm(request: Request, db=Depends(get_session)):
    """The confirmation page's answer (a plain form post from the system
    browser). ``confirm`` applies what X proved; ``cancel`` discards it."""
    form = parse_qs((await request.body()).decode("utf-8", "replace"), max_num_fields=4)
    token = (form.get("token") or [""])[0]
    decision = (form.get("decision") or [""])[0]
    try:
        result = await svc.decide_confirmation(db, token=token, decision=decision)
    except Exception:
        logger.exception("x oauth confirm failed")
        result = svc.CallbackResult(
            "Something Went Wrong",
            "We couldn't complete verification. Open Loudrr again to retry.",
            False, 500,
        )
    return HTMLResponse(
        content=_callback_html(result.title, result.message, result.success),
        status_code=result.status_code, headers=_PAGE_HEADERS,
    )


# ---- public OAuth callback for the pre-signup WAITLIST flow ----
# X redirects the browser here after the applicant authorizes; we consume the
# state, exchange the code, mint a signed proof, and 302 the browser back to
# the frontend's /waitlist/oauth-return page. That page pulls the proof out of
# the URL and hands it back to the form so submit can go through with the
# OAuth-verified handle.
@router.get("/api/auth/x/callback/waitlist/")
@limiter.limit("30/minute")  # public unauthenticated endpoint — cap abuse
async def x_oauth_callback_waitlist(
    request: Request,                                # slowapi needs the IP
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    db=Depends(get_session),
) -> RedirectResponse:
    try:
        return await waitlist_oauth_svc.handle_waitlist_callback(
            db, code=code, state=state, error=error,
        )
    except Exception:
        logger.exception("waitlist x oauth callback failed")
        return waitlist_oauth_svc.redirect_to_frontend(error="internal")
