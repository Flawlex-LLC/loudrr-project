"""Kill switches — the boolean site settings that let an admin stop one write
path (or everything) without a deploy.

Two things make these different from every other site setting:

1. **They are read fresh, every time.** `site_settings.get_setting` caches for
   300s per process, which is fine for a tier multiplier and useless for an
   emergency stop: flipping MAINTENANCE_MODE would take up to five minutes to
   reach the other uvicorn workers and the arq worker. Each check here pops
   the key out of that cache first, so the read is a single indexed SELECT
   against the live row and the switch is effective everywhere immediately.
   These run only on write paths (submit a post, queue a claim, register,
   ingest a sponsor tweet), so the extra query is noise.

2. **MAINTENANCE_MODE is a master.** Any gated path refuses while it is on,
   regardless of that path's own switch.

Turning a switch off refuses NEW work only — nothing in flight is cancelled
and no escrow is touched.
"""
import logging

from app.core.errors import ServiceUnavailable
from app.models.site_setting import SiteSetting  # noqa: F401 — documents the source table
from app.services import site_settings
from app.services.site_settings import get_setting

logger = logging.getLogger(__name__)

# Keys, with the value that applies when the row has never been seeded.
# Defaults are "open" (except MAINTENANCE_MODE) so a missing row can never
# take the product down by accident.
MAINTENANCE_MODE = "MAINTENANCE_MODE"
POSTS_ENABLED = "POSTS_ENABLED"
CLAIMS_ENABLED = "CLAIMS_ENABLED"
WAITLIST_REGISTRATION_ENABLED = "WAITLIST_REGISTRATION_ENABLED"
SPONSOR_INGEST_ENABLED = "SPONSOR_INGEST_ENABLED"

DEFAULTS: dict[str, bool] = {
    MAINTENANCE_MODE: False,
    POSTS_ENABLED: True,
    CLAIMS_ENABLED: True,
    WAITLIST_REGISTRATION_ENABLED: True,
    SPONSOR_INGEST_ENABLED: True,
}

ALL_KEYS: tuple[str, ...] = tuple(DEFAULTS)

MAINTENANCE_MESSAGE = (
    "Loudrr is in maintenance right now. Nothing has been lost — try again in a few minutes."
)


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


async def _read(db, key: str) -> bool:
    """Current value of one switch, bypassing the 300s settings cache."""
    site_settings._cache.pop(key, None)
    value = await get_setting(db, key, DEFAULTS.get(key, True))
    return _as_bool(value)


async def is_enabled(db, key: str) -> bool:
    """True when `key` is on AND maintenance mode is off."""
    if key != MAINTENANCE_MODE and await _read(db, MAINTENANCE_MODE):
        return False
    return await _read(db, key)


async def require_enabled(db, key: str, message: str) -> None:
    """Raise 503 with a user-facing message when the path is switched off."""
    if key != MAINTENANCE_MODE and await _read(db, MAINTENANCE_MODE):
        logger.info("kill switch: %s refused — MAINTENANCE_MODE is on", key)
        raise ServiceUnavailable(MAINTENANCE_MESSAGE)
    if not await _read(db, key):
        logger.info("kill switch: %s refused — switch is off", key)
        raise ServiceUnavailable(message)


async def blocked_reason(db, key: str, message: str) -> str | None:
    """Non-raising variant for endpoints that answer with a body + status
    instead of an exception (the claim queue). Returns the message to show,
    or None when the path is open."""
    if key != MAINTENANCE_MODE and await _read(db, MAINTENANCE_MODE):
        return MAINTENANCE_MESSAGE
    if not await _read(db, key):
        return message
    return None
