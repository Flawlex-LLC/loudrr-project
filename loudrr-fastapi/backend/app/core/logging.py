"""Structured logging via structlog.

Routes stdlib logging through structlog so every `logging.getLogger(__name__)`
call site (services, integrations, integrations/x_oauth.py, tasks/*) gets
structured output automatically — no caller changes needed.

DEV (settings.debug=True):
  Pretty colored key=value output, easy to scan in a terminal.
    2026-06-06T13:24:51.812341Z [info] x-verification approved [app.services.x_verification]
        user_id=cf64b265 ... claimed=@0xBlest_

PROD (settings.debug=False):
  Single-line JSON per event, ready for any log aggregator (CloudWatch, Loki,
  GlitchTip's log shipping, etc.).
    {"event":"x-verification approved","logger":"app.services.x_verification","level":"info","timestamp":"2026-06-06T13:24:51.812341Z","user_id":"cf64b265","claimed":"@0xBlest_"}

Request-scoped context (request_id, user_id, telegram_id) goes via
`structlog.contextvars.bind_contextvars()` in a middleware — left as a
follow-up since the contract here is just "route loggers"; request binding is
orthogonal.
"""
import logging
import sys

import structlog


def configure_logging(*, debug: bool, log_level: str = "INFO") -> None:
    """Initialize structlog and reroute stdlib logging through it.

    Idempotent — safe to call from config.py at import time and again from
    tests / repls. Clears existing handlers on the root logger so successive
    calls don't double-log.
    """
    level = getattr(logging, log_level.upper(), logging.INFO)

    # Processors that run on EVERY log event regardless of source (stdlib or
    # structlog-native). Order matters — earlier processors mutate the event
    # dict that later processors render.
    shared_processors: list = [
        # Merge any bind_contextvars()-set fields (request_id etc.)
        structlog.contextvars.merge_contextvars,
        # Add the logger name (e.g. "app.services.users")
        structlog.stdlib.add_logger_name,
        # Add the level name ("info", "warning")
        structlog.stdlib.add_log_level,
        # ISO-8601 UTC timestamp under "timestamp"
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        # If exc_info is set, attach a formatted traceback
        structlog.processors.format_exc_info,
        # If stack_info was requested, attach a stack snapshot
        structlog.processors.StackInfoRenderer(),
    ]

    # The final renderer — pretty in dev, JSON in prod.
    if debug:
        renderer: structlog.types.Processor = structlog.dev.ConsoleRenderer(colors=True)
    else:
        renderer = structlog.processors.JSONRenderer()

    # Configure structlog's own loggers (structlog.get_logger() callers).
    structlog.configure(
        processors=[
            *shared_processors,
            # Hand off to ProcessorFormatter so stdlib-routed events use the
            # same final renderer.
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Build a ProcessorFormatter for the stdlib root handler. The
    # foreign_pre_chain runs on records that come from stdlib loggers
    # (logging.getLogger(__name__).info(...)) so they pick up the same
    # processors as native structlog calls.
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            # Drop internal _record / _from_structlog keys before rendering
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    # Clear existing handlers so repeated calls don't double-log
    root.handlers[:] = [handler]
    root.setLevel(level)

    # Quiet down a few notoriously noisy third-party loggers in dev. Keep them
    # at INFO so structured events still flow; turn them off only for echo.
    # SQLAlchemy engine echo (when settings.debug=True the engine prints every
    # query — that's intentional, leave it).
    if not debug:
        # In prod, drop SQLAlchemy's row-by-row tracing to WARNING.
        logging.getLogger("sqlalchemy.engine.Engine").setLevel(logging.WARNING)
