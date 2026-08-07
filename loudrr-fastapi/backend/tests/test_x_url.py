"""Unit tests for the pure X-username extractor (no DB, no async)."""
import pytest

from app.services.x_url import extract_x_username


@pytest.mark.parametrize("raw, expected", [
    ("AltcoinGordon", "AltcoinGordon"),          # bare username
    ("@AltcoinGordon", "AltcoinGordon"),         # leading @
    ("  @jack  ", "jack"),                        # whitespace trimmed
    ("https://x.com/jack", "jack"),               # full URL
    ("https://twitter.com/jack", "jack"),         # twitter.com host
    ("https://www.x.com/jack", "jack"),           # www host
    ("x.com/jack", "jack"),                        # no scheme
    ("https://x.com/jack/status/123", "jack"),    # first path part is the user
])
def test_extract_valid(raw, expected):
    assert extract_x_username(raw) == expected


@pytest.mark.parametrize("raw", [
    "https://x.com/i/status/123",   # /i system path
    "https://x.com/home",           # system path
    "https://x.com/settings",       # system path
    "https://instagram.com/jack",   # wrong host
    "https://x.com/",               # no username at all
    "https://x.com/waytoolongusername",  # > 15 chars, invalid handle
    "not a url at all !!!",          # garbage
    "",                              # empty
])
def test_extract_invalid(raw):
    assert extract_x_username(raw) is None