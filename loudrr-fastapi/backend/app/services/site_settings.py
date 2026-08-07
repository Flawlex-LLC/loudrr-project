from sqlalchemy import select
from app.models.site_setting import SiteSetting
from decimal import Decimal
import time

# in-process cache for site settings to avoid hitting the database every time
_cache: dict = {}
_TTL_SECONDS = 300  # cache settings for 5 minutes

# sentinel so `default=None` is distinguishable from "no default given"
_MISSING = object()


def _coerce(value: str, data_type: str):
    """Interpret a stored string per its data_type hint."""
    if data_type == "int":
        return int(value)
    if data_type == "float":
        return float(value)
    if data_type == "decimal":
        return Decimal(value)
    if data_type == "bool":
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return value  # "str" / unknown — store/return as-is


async def get_setting(db, key: str, default=_MISSING):
    """Read a setting by key, cached for 5 minutes so it doesn't hit the
    database on every request. If the key is missing: raise KeyError, unless
    a `default` is supplied — then return it (and don't cache the default)."""
    cached = _cache.get(key)
    if cached and time.time() - cached[1] < _TTL_SECONDS:
        return cached[0]

    result = await db.execute(select(SiteSetting).where(SiteSetting.key == key))
    setting = result.scalar_one_or_none()
    if setting is None:
        if default is not _MISSING:
            return default
        raise KeyError(f"Setting with key '{key}' not found - seed it first!")

    value = _coerce(setting.value, setting.data_type)
    _cache[key] = (value, time.time())
    return value
