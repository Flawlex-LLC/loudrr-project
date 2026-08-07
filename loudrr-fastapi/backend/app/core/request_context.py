"""Per-request context middleware: request ID + structured-log binding.

Binds a UUID (or the caller-supplied X-Request-Id header) to structlog's
contextvars so EVERY log line for this request — wherever it originates,
services / integrations / SQLAlchemy echo / uvicorn — carries the same
request_id, method, and path. Surfaces the ID back to the caller via the
X-Request-Id response header for correlation across services and in
GlitchTip / Sentry breadcrumbs.

structlog's processor chain (app/core/logging.py) already includes
`structlog.contextvars.merge_contextvars` — this middleware is the
producer that puts values into that chain. Without this middleware, the
processor merges an empty dict and logs look the same as before.

WHY a class-based BaseHTTPMiddleware (vs @app.middleware decorator):
  Decorator-style middleware in FastAPI runs INSIDE the route's request
  scope, after Starlette's middleware stack has unwound. That means a log
  line emitted from an exception handler — or from the security-headers
  middleware itself — would lose the bound contextvars. BaseHTTPMiddleware
  wraps everything, including other middlewares, so the request_id stays
  available for the entire request lifecycle.

WHY clear_contextvars() in finally:
  contextvars are inherited by child tasks. If we DIDN'T clear at the end
  of a request, the next request handled on the same asyncio task could
  inherit the previous request's id (a classic logging "stuck on one
  user" bug). The finally guarantees cleanup even when call_next raises.
"""
import uuid
from typing import Awaitable, Callable

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

REQUEST_ID_HEADER = "X-Request-Id"


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Generate or honor a request ID, bind it to structlog, echo it back."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        # Honor an incoming X-Request-Id from upstream (load balancer, frontend,
        # an integration retrying with the same id). Otherwise mint a fresh
        # uuid4. Cap length to avoid header-injection mischief.
        incoming = request.headers.get(REQUEST_ID_HEADER, "").strip()
        request_id = incoming[:80] if incoming else uuid.uuid4().hex

        # Bind the per-request fields. Anything else (user_id once auth resolves,
        # a feature flag, etc.) can be added by a dep / service via the same
        # bind_contextvars call site — they all merge into one logged dict.
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
        )

        # The debug `?telegram_id=` bypass is the only easy way to identify
        # the user pre-auth-resolution, so we bind it eagerly when present.
        # Real auth dependencies can rebind to the resolved UUID later via
        # bind_contextvars(user_id=...).
        if tg := request.query_params.get("telegram_id"):
            structlog.contextvars.bind_contextvars(telegram_id=tg)

        try:
            response = await call_next(request)
        except Exception as exc:
            # Pre-empt the BaseHTTPMiddleware-vs-sentry-auto-capture quirk:
            # when this middleware's stack handling swallows the exception
            # context, the SDK's middleware-level auto-capture can silently
            # miss it. Explicit capture here guarantees the report fires
            # exactly once. No-op if sentry_sdk.init() was never called.
            import sentry_sdk
            sentry_sdk.capture_exception(exc)
            raise
        finally:
            # Clear so the next request on this asyncio task starts fresh.
            structlog.contextvars.clear_contextvars()

        response.headers[REQUEST_ID_HEADER] = request_id
        return response
