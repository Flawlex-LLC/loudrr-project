import re
from urllib.parse import urlparse

_X_USERNAME = re.compile(r"^[A-Za-z0-9_]{1,15}$")
_X_HOSTS = {"x.com", "twitter.com", "www.x.com", "www.twitter.com"}

# X / Twitter system paths — URL routes, NOT usernames.
# Without this set, /x.com/home would parse as username "home".
# Matches the production list at loudrr/backend/core/x_url_resolver.py.
_X_SYSTEM_PATHS = frozenset({
    "i", "home", "explore", "search", "notifications", "messages",
    "settings", "compose", "intent", "hashtag", "tos", "privacy",
    "about", "help", "login", "signup", "logout",
})


def extract_x_username(x_link: str) -> str | None:
    """Extract an X username from a URL or bare @username, or None.
    Rejects /i/status/..., X system paths, and obviously bad input."""
    s = x_link.strip().lstrip("@")

    # bare username?
    if _X_USERNAME.match(s):
        return s

    # parse as URL — urlparse raises ValueError on malformed input (e.g. a bare
    # "[" looks like a broken IPv6 host), so treat any parse failure as "not a handle"
    try:
        parsed = urlparse(s if "://" in s else f"https://{s}")
    except ValueError:
        return None
    if (parsed.netloc or "").lower() not in _X_HOSTS:
        return None

    parts = [p for p in parsed.path.split("/") if p]
    if not parts:
        return None
    if parts[0].lower() in _X_SYSTEM_PATHS:
        return None

    return parts[0] if _X_USERNAME.match(parts[0]) else None
