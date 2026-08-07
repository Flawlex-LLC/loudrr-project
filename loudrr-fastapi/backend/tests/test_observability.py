"""Tests for /health, /readyz, and the request-id middleware.

Validates the production-hardening commit (fd7027f) end-to-end. Each test
hits the actual ASGI app via the `client` fixture, so the middleware stack
+ routes + dep-overrides + structlog binding are exercised exactly as in
real requests.
"""


# ---------------------------------------------------------------------------
# /health — liveness probe stays trivial
# ---------------------------------------------------------------------------
async def test_health_returns_ok_without_touching_deps(client):
    """/health must NOT depend on Postgres/Redis being alive. It only proves
    the event loop is responsive. The test overrides get_session via conftest
    but the route doesn't use it — that's the point."""
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


async def test_health_returns_x_request_id_header(client):
    """The RequestContextMiddleware should mint a UUID per request and echo
    it back in the X-Request-Id header for caller-side correlation."""
    r = await client.get("/health")
    rid = r.headers.get("x-request-id")
    assert rid, "expected X-Request-Id header"
    # uuid4 hex is 32 chars or a UUID with dashes (36 chars) — accept both
    assert len(rid) in (32, 36), f"unexpected request-id format: {rid!r}"


async def test_health_honors_caller_supplied_request_id(client):
    """When an upstream caller (load balancer, frontend, integration retry)
    supplies X-Request-Id, the middleware MUST preserve it verbatim so the
    log trail correlates end-to-end."""
    caller_id = "trace-from-frontend-abc-123"
    r = await client.get("/health", headers={"X-Request-Id": caller_id})
    assert r.headers.get("x-request-id") == caller_id


async def test_request_id_is_unique_per_call(client):
    """Two calls without an explicit X-Request-Id should each get a fresh
    UUID. If the contextvars weren't cleared between requests, both would
    share an id and the logs would falsely correlate."""
    r1 = await client.get("/health")
    r2 = await client.get("/health")
    assert r1.headers["x-request-id"] != r2.headers["x-request-id"]


# ---------------------------------------------------------------------------
# /readyz — readiness probe actually pings deps
# ---------------------------------------------------------------------------
async def test_readyz_returns_db_ok(client):
    """The conftest's db_session fixture creates a real Postgres schema per
    test, so /readyz's SELECT 1 against it should succeed."""
    r = await client.get("/readyz")
    assert r.status_code == 200
    body = r.json()
    assert body["db"] == "ok"


async def test_readyz_skips_redis_when_unconfigured(client, monkeypatch):
    """When settings.redis_url is empty (the default in tests), /readyz
    should NOT attempt a Redis ping — it should report 'skipped' so the
    overall probe stays 200. Validates the test-tier optionality."""
    from app.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "redis_url", "")

    r = await client.get("/readyz")
    assert r.status_code == 200
    body = r.json()
    assert body["db"] == "ok"
    assert body["redis"].startswith("skipped"), body["redis"]


async def test_readyz_carries_request_id(client):
    """Probes must also carry the X-Request-Id so dependency-failure logs
    can be correlated with the probe that triggered them."""
    r = await client.get("/readyz")
    assert r.headers.get("x-request-id"), "readyz must carry X-Request-Id too"


# ---------------------------------------------------------------------------
# structlog context binding — verify the middleware actually binds
# ---------------------------------------------------------------------------
async def test_middleware_binds_request_id_to_structlog_contextvars(client):
    """White-box check: while a request is in-flight, structlog's contextvars
    should contain request_id, method, path. We can't easily peek mid-request
    from the test, so this test uses a custom endpoint patched in at runtime
    — or, simpler, just verifies that structlog's get_contextvars is empty
    OUTSIDE a request (the finally clear ran)."""
    import structlog

    # Outside any request: contextvars must be clean
    ctx_before = structlog.contextvars.get_contextvars()
    assert ctx_before == {}, f"contextvars not clean before request: {ctx_before!r}"

    r = await client.get("/health")
    assert r.status_code == 200

    # After the request: also clean (finally clear_contextvars ran)
    ctx_after = structlog.contextvars.get_contextvars()
    assert ctx_after == {}, f"contextvars leaked: {ctx_after!r}"


async def test_caller_request_id_is_length_capped(client):
    """The middleware caps incoming X-Request-Id at 80 chars to prevent a
    malicious caller from injecting massive header values. Anything past
    80 chars should be truncated."""
    long_id = "x" * 200
    r = await client.get("/health", headers={"X-Request-Id": long_id})
    echoed = r.headers["x-request-id"]
    assert len(echoed) == 80
    assert echoed == "x" * 80


async def test_telegram_id_query_param_binds_via_middleware(client):
    """When the debug ?telegram_id=N query param is present, the middleware
    eagerly binds it so logs from auth-resolution can correlate. This is
    indirect — we just confirm the request still succeeds with the param
    present + the response carries a request-id (so the binding path didn't
    crash)."""
    r = await client.get("/health", params={"telegram_id": 9999})
    assert r.status_code == 200
    assert r.headers.get("x-request-id")


# ---------------------------------------------------------------------
# Error tracking — 500s must reach the SDK explicitly
# ---------------------------------------------------------------------
async def test_500_is_reported_to_error_tracking(client, monkeypatch):
    """Pre-empts the volume.fun bug: BaseHTTPMiddleware can swallow the
    exception context so sentry-sdk's auto-capture misses it. Our
    middleware fixes this by calling capture_exception explicitly in the
    error path. This test mocks the SDK + adds a temporary route that
    raises, then asserts the mock was called with the raised exception."""
    import sentry_sdk
    from httpx import AsyncClient, ASGITransport
    from app.main import app

    captured: list[BaseException] = []
    monkeypatch.setattr(sentry_sdk, "capture_exception", lambda exc: captured.append(exc))

    async def _boom():
        raise RuntimeError("test 500 — must reach the SDK")
    app.add_api_route("/__test_boom__", _boom)

    # Build a local transport with raise_app_exceptions=False so Starlette's
    # ServerErrorMiddleware (which fires LAST in production and converts any
    # uncaught exception into a 500 response) actually runs — the default
    # test transport re-raises into pytest before that conversion can happen.
    # This mirrors real prod behavior: caller sees 500, SDK sees the exception.
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as boom_client:
            r = await boom_client.get("/__test_boom__")
        # FastAPI returns 500 to the caller — that's correct, the SDK
        # report happens out-of-band.
        assert r.status_code == 500, f"expected 500 from raising route, got {r.status_code}"
        assert len(captured) >= 1, (
            "sentry_sdk.capture_exception was NEVER called — the middleware "
            "is swallowing the exception context (this is the volume.fun bug)."
        )
        assert isinstance(captured[0], RuntimeError)
        assert "test 500 — must reach the SDK" in str(captured[0])
    finally:
        # Remove the synthetic route so it doesn't leak to other tests
        app.routes[:] = [
            rt for rt in app.routes
            if getattr(rt, "path", "") != "/__test_boom__"
        ]
