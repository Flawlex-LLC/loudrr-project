"""Prod-guard: config.py refuses to boot with dev-only settings + ENVIRONMENT=prod.

This test is the SAFETY NET against the audit's #1 GTM risk: shipping with
`DEBUG=True` (which activates the `?telegram_id=` auth bypass — any user can
impersonate any other user with a query param). The guard fires at module
import time when ENVIRONMENT=prod combines with any dev-only value.
"""
import sys

import pytest


def _reimport_config():
    """Force a fresh import of app.core.config so the module-level guard
    at config.py:118 re-runs against the current os.environ."""
    for mod in list(sys.modules):
        if mod.startswith("app.core.config") or mod == "app.core.logging":
            del sys.modules[mod]
    import app.core.config  # noqa: F401


def test_prod_guard_fires_when_debug_true(monkeypatch):
    """ENVIRONMENT=prod + DEBUG=True must refuse to boot — this is the
    catastrophic footgun the audit flagged as the #1 pre-GTM risk."""
    monkeypatch.setenv("ENVIRONMENT", "prod")
    monkeypatch.setenv("DEBUG", "True")
    monkeypatch.setenv("SECRET_KEY", "a-real-secret-32-chars-long-here!")
    monkeypatch.setenv("ADMIN_PASSWORD", "not-empty")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    with pytest.raises(RuntimeError, match="DEBUG=True"):
        _reimport_config()


def test_prod_guard_fires_on_default_secret_key(monkeypatch):
    """ENVIRONMENT=prod + SECRET_KEY untouched must refuse to boot — the
    dev default is public knowledge and would let an attacker forge
    SQLAdmin sessions."""
    monkeypatch.setenv("ENVIRONMENT", "prod")
    monkeypatch.setenv("DEBUG", "False")
    monkeypatch.delenv("SECRET_KEY", raising=False)  # falls back to config default
    monkeypatch.setenv("ADMIN_PASSWORD", "not-empty")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    with pytest.raises(RuntimeError, match="SECRET_KEY is the dev default"):
        _reimport_config()


def test_prod_guard_fires_on_empty_admin_password(monkeypatch):
    """Blank ADMIN_PASSWORD disables SQLAdmin auth entirely — refuse."""
    monkeypatch.setenv("ENVIRONMENT", "prod")
    monkeypatch.setenv("DEBUG", "False")
    monkeypatch.setenv("SECRET_KEY", "a-real-secret-32-chars-long-here!")
    monkeypatch.setenv("ADMIN_PASSWORD", "")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    with pytest.raises(RuntimeError, match="ADMIN_PASSWORD is empty"):
        _reimport_config()


def test_prod_guard_fires_on_missing_bot_token(monkeypatch):
    """Blank TELEGRAM_BOT_TOKEN → HMAC verification is impossible; every
    real user gets 401."""
    monkeypatch.setenv("ENVIRONMENT", "prod")
    monkeypatch.setenv("DEBUG", "False")
    monkeypatch.setenv("SECRET_KEY", "a-real-secret-32-chars-long-here!")
    monkeypatch.setenv("ADMIN_PASSWORD", "not-empty")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    with pytest.raises(RuntimeError, match="TELEGRAM_BOT_TOKEN is empty"):
        _reimport_config()


def test_prod_guard_silent_when_environment_not_set(monkeypatch):
    """Without ENVIRONMENT=prod, the guard is a no-op — local dev is
    frictionless (blank secret + DEBUG=True must still work)."""
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.setenv("DEBUG", "True")
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    _reimport_config()  # must not raise


def test_prod_guard_passes_with_valid_prod_config(monkeypatch):
    """Well-configured prod → import succeeds cleanly, no warnings."""
    monkeypatch.setenv("ENVIRONMENT", "prod")
    monkeypatch.setenv("DEBUG", "False")
    monkeypatch.setenv("SECRET_KEY", "a-real-32-plus-char-production-secret!")
    monkeypatch.setenv("ADMIN_PASSWORD", "strong-admin-password-here")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456789:abcdefg")
    _reimport_config()  # must not raise
