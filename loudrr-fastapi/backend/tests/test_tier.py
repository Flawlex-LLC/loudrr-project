"""Unit tests for the tier system (spec §5.3) — pure functions, no DB."""
from decimal import Decimal

import pytest

from app.services import tier


@pytest.mark.parametrize(
    "score,expected",
    [
        (0, "Anon"),
        (99, "Anon"),
        (100, "Normie"),
        (199, "Normie"),
        (200, "Degen"),
        (400, "Based"),
        (600, "Legend"),
        (800, "OG"),
        (1000, "GOAT"),
        (5000, "GOAT"),
    ],
)
def test_tier_for(score, expected):
    assert tier.tier_for(score) == expected


@pytest.mark.parametrize(
    "score,expected",
    [
        (0, "1.00"),
        (100, "1.10"),
        (200, "1.15"),
        (400, "1.20"),
        (600, "1.25"),
        (800, "1.30"),
        (1000, "1.35"),
    ],
)
def test_multiplier_for(score, expected):
    assert tier.multiplier_for(score) == Decimal(expected)


def test_karma_for_is_4dp_and_no_inflation():
    karma, mult = tier.karma_for(Decimal("1"), 400)
    assert mult == Decimal("1.20")
    assert karma == Decimal("1.2000")
    # the quantization is exactly 4 decimal places
    assert karma.as_tuple().exponent == -4


def test_karma_for_anon_is_base():
    karma, mult = tier.karma_for(Decimal("1"), 0)
    assert (karma, mult) == (Decimal("1.0000"), Decimal("1.00"))
