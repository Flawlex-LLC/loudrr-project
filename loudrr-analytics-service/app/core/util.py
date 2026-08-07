"""Small shared helpers."""
from __future__ import annotations

import re
from datetime import datetime, timezone

_LINK_RE = re.compile(r"(?:twitter\.com|x\.com)/(@?\w+)", re.IGNORECASE)

_TW_FMT = "%a %b %d %H:%M:%S %z %Y"  # "Mon Jun 29 06:21:12 +0000 2026"


def parse_twitter_dt(s: str | None) -> datetime:
    """Twitter ``createdAt`` -> UTC-naive datetime (portable across SQLite/Postgres).

    Single shared implementation — mindshare and engagement MUST bucket the same tweet
    into the same day, so this must never fork. Bad/missing input falls back to now().
    """
    try:
        dt = datetime.strptime(s, _TW_FMT) if s else datetime.now(timezone.utc)
    except (ValueError, TypeError):
        dt = datetime.now(timezone.utc)
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def username_from_link(link: str) -> str | None:
    """Extract a handle from an x.com/twitter.com URL, or None.

    Ignores non-profile paths' extra segments (status/, i/, etc.) — only the first
    path segment is treated as the handle.
    """
    m = _LINK_RE.search(link or "")
    if not m:
        return None
    handle = m.group(1).lstrip("@")
    # guard against x.com/i/... , x.com/home, etc. that aren't profiles
    return handle if handle.lower() not in {"i", "home", "search", "explore"} else None
