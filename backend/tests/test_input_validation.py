"""Input-validation & hostile-input hardening.

Bad input is rejected at the edge (Pydantic / parsers) before it can reach the
DB, and the service caps/normalizes what it stores. Complements test_x_url.py
(which covers the standard valid/invalid matrix) with the nasty cases.
"""
from types import SimpleNamespace

import pytest

from app.schemas.waitlist import OtherPlatform, OtherPlatformKind
from app.services import waitlist as waitlist_svc
from app.services.x_url import extract_x_username


# ---- X username extraction: hostile inputs all resolve to None ----
@pytest.mark.parametrize(
    "bad",
    [
        "café",                       # non-ASCII letters
        "naïve_user",                 # mixed unicode
        "你好",                        # CJK
        "a" * 16,                     # 16 chars — one past the 15 limit
        "https://x.com/" + "a" * 16,  # overlong username in a URL path
        "user name",                  # space
        "user-name",                  # hyphen not allowed
        "https://x.com/",             # no path
        "https://notx.com/user",      # wrong host
        "",                           # empty
        "   ",                        # whitespace only
    ],
)
def test_extract_x_username_rejects_hostile(bad):
    assert extract_x_username(bad) is None


# ---- waitlist register endpoint: malformed body is a 422, never a 500 ----
async def test_register_missing_x_link_422(client):
    r = await client.post(
        "/waitlist/register/",
        params={"telegram_id": 9_500_002},
        json={},  # x_link is now the only required field
    )
    assert r.status_code == 422


# ---- service caps / normalizes what it stores ----
async def test_other_platforms_capped_at_five(db_session):
    payload = SimpleNamespace(
        x_link="https://x.com/capuser",
        region=None,
        niche=None,
        referral_code=None,
        other_platforms=[
            OtherPlatform(platform=OtherPlatformKind.OTHER, username=f"u{i}")
            for i in range(7)
        ],
    )
    result = await waitlist_svc.register_entry(
        db_session, tg_user={"id": 9_500_003}, payload=payload
    )
    assert len(result.entry.other_platforms) == 5  # 7 submitted, stored 5


async def test_large_telegram_id_is_accepted(db_session):
    """Telegram IDs are 64-bit — a value past 32-bit must store fine (BigInteger)."""
    big = 8_888_888_888  # > 2**32
    payload = SimpleNamespace(
        x_link="https://x.com/biguser",
        region=None,
        niche=None,
        referral_code=None,
        other_platforms=[],
    )
    result = await waitlist_svc.register_entry(
        db_session, tg_user={"id": big}, payload=payload
    )
    assert result.entry.telegram_id == big
