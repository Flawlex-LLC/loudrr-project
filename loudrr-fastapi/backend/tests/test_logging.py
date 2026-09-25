"""Logging must never write the Telegram bot token.

httpx logs "HTTP Request: GET https://api.telegram.org/bot<token>/..." at
INFO, so its loggers have to sit above INFO in every environment.
"""
import logging

import pytest

from app.core.config import settings
from app.core.logging import configure_logging


@pytest.mark.parametrize("debug", [False, True])
def test_http_client_loggers_do_not_log_request_urls(debug):
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    try:
        configure_logging(debug=debug, log_level="DEBUG")
        for name in ("httpx", "httpcore"):
            assert not logging.getLogger(name).isEnabledFor(logging.INFO), name
    finally:
        configure_logging(debug=settings.debug, log_level=settings.log_level)
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)
