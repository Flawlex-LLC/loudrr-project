"""Truth-check: the `live` flag in app/core/site_settings_meta.py must
match reality - a key is `live=True` iff some code in app/services,
app/api, or app/tasks reads it via `get_setting(db, "KEY"`.

Failure modes caught:
  - `live=True` but no `get_setting(db, "KEY"` exists  -> the badge lies
  - `live=False` but a `get_setting(db, "KEY"` exists   -> wired but unflipped

Notes on dynamic keys
---------------------
Some keys aren't passed as a literal to `get_setting` - they're stored in a
nearby table and dispatched through a variable:

  * `app/services/outbox.py` keeps `_TEMPLATE_BY_EVENT = {event: ("TG_MSG_X",
    default)}` and then calls `get_setting(db, key, default=...)`.
  * `app/services/streaks.py` keeps `_BANDS = ((30, "STREAK_30_DAY_MULTIPLIER",
    "STREAK_30_DAY_BONUS", ...), ...)` then `get_setting(db, mul_key, ...)`.
  * `app/services/tier.py` builds `f"TIER_{suffix}_THRESHOLD"` /
    `f"TIER_{suffix}_MULTIPLIER"` from `_TIER_KEYS` and passes those.

For files that contain ANY `get_setting(...)` call we therefore also harvest
all-uppercase string literals (and `f"TIER_{...}_THRESHOLD"`-style f-string
templates) and treat those as live reads too. This catches the dispatch-by-
variable pattern without an opaque whitelist.
"""
import re
from pathlib import Path

from app.core.site_settings_meta import ALL_GROUPS

ROOT = Path(__file__).resolve().parent.parent / "app"

# Direct call: get_setting(db, "KEY"...) or get_setting(self.db, "KEY"...).
_DIRECT_RE = re.compile(
    r"""get_setting\([^,]+,\s*["']([A-Z_][A-Z0-9_]*)["']"""
)

# All-caps string literal, e.g. "TG_MSG_WAITLIST_SUBMITTED" or
# "STREAK_30_DAY_BONUS" - used to catch dispatch-by-variable in outbox /
# streaks where the key lives in a tuple/dict near a get_setting call.
_LITERAL_KEY_RE = re.compile(r"""["']([A-Z][A-Z0-9_]{3,})["']""")

# f-string-built key, e.g. f"TIER_{key_suffix}_THRESHOLD". We expand each
# such template against the cartesian product of tier suffixes used in
# app/services/tier.py.
_FSTRING_KEY_RE = re.compile(
    r"""f["']([A-Z][A-Z0-9_]*)\{[^}]+\}([A-Z0-9_]*)["']"""
)

# Suffixes that the f-string-built TIER_* keys can take. Hardcoded from
# app/services/tier.py:_TIER_KEYS - mirrors the live tier set.
_TIER_SUFFIXES = ("ANON", "NORMIE", "DEGEN", "BASED", "LEGEND", "OG", "GOAT")


def _scan_for_keys() -> set[str]:
    """Walk app/ and collect every SiteSetting key that is read at runtime.

    Sources, in order of confidence:
      1. Literal `get_setting(db, "KEY"` calls.
      2. f-string templates like `f"TIER_{x}_THRESHOLD"` - expanded against
         the known tier-suffix universe.
      3. All-uppercase string literals living in a file that also contains
         at least one `get_setting(` call (catches the dispatch-by-variable
         pattern in outbox.py / streaks.py).
    """
    found: set[str] = set()
    for path in ROOT.rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")

        # 1) literal get_setting(db, "KEY"
        for m in _DIRECT_RE.finditer(text):
            found.add(m.group(1))

        # Only mine dispatch-by-variable patterns from files that actually
        # call get_setting - keeps the false-positive rate from random
        # all-caps strings (event_type enum values etc.) low.
        if "get_setting(" not in text:
            continue

        # 2) f-string-built keys e.g. f"TIER_{x}_THRESHOLD" - expand against
        # the known tier suffix universe.
        for prefix, suffix in _FSTRING_KEY_RE.findall(text):
            for tier in _TIER_SUFFIXES:
                # prefix is something like "TIER_" (trailing _ kept), suffix
                # is "_THRESHOLD" or "_MULTIPLIER". Avoid double underscores
                # when one side already has trailing/leading _.
                key = f"{prefix}{tier}{suffix}"
                found.add(key)

        # 3) all-caps string literals in a file that calls get_setting -
        # catches outbox.py's _TEMPLATE_BY_EVENT and streaks.py's _BANDS.
        for m in _LITERAL_KEY_RE.finditer(text):
            found.add(m.group(1))

    return found


def test_live_flag_matches_reality():
    read_at_runtime = _scan_for_keys()
    expected_live = {s.key for g in ALL_GROUPS for s in g.settings if s.live}
    expected_not_live = {
        s.key for g in ALL_GROUPS for s in g.settings if not s.live
    }

    lying_live = expected_live - read_at_runtime
    hidden_wired = expected_not_live & read_at_runtime

    msg_parts: list[str] = []
    if lying_live:
        msg_parts.append(
            "Settings flagged live=True but with NO get_setting call site: "
            f"{sorted(lying_live)}"
        )
    if hidden_wired:
        msg_parts.append(
            "Settings flagged live=False but get_setting IS called: "
            f"{sorted(hidden_wired)}"
        )
    assert not msg_parts, "\n".join(msg_parts)
