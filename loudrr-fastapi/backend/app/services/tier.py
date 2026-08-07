"""The tier system — TweetScout score → tier name + karma multiplier.

The canonical thresholds/multipliers (spec §5.3). A higher score means
higher-quality engagement, so a higher multiplier on every karma earned.

    Tier    | Min score | Multiplier
    Anon    |        0  |   1.00
    Normie  |      100  |   1.10
    Degen   |      200  |   1.15
    Based   |      400  |   1.20
    Legend  |      600  |   1.25
    OG      |      800  |   1.30
    GOAT    |     1000  |   1.35

Karma uses ROUND_HALF_EVEN (banker's rounding) to 4 dp — the karma deducted
from a post's escrow equals the karma credited to the engager, so there is no
inflation. The tier names match what the live frontend already renders.

These mirror the `TIER_*_THRESHOLD` / `TIER_*_MULTIPLIER` dynamic settings —
the hardcoded values below are the defaults; on startup (and after any admin
update of a TIER_* SiteSetting) `load_tiers_from_settings(db)` rebuilds the
TIERS list in place from the DB. tier_for/multiplier_for stay sync so the many
call sites (services/users.py etc.) don't need to be threaded with await.
"""
import logging
from decimal import Decimal, ROUND_HALF_EVEN, InvalidOperation

logger = logging.getLogger(__name__)

# 4-dp precision for all internal karma math
KARMA_QUANTIZE = Decimal("0.0001")

# (name, min_score, multiplier) — ordered HIGHEST threshold first so the
# first match wins on a simple top-down scan.
TIERS: list[tuple[str, int, Decimal]] = [
    ("GOAT", 1000, Decimal("1.35")),
    ("OG", 800, Decimal("1.30")),
    ("Legend", 600, Decimal("1.25")),
    ("Based", 400, Decimal("1.20")),
    ("Degen", 200, Decimal("1.15")),
    ("Normie", 100, Decimal("1.10")),
    ("Anon", 0, Decimal("1.00")),
]

# The canonical tier keys (uppercase, matches the SiteSetting suffixes).
# Order mirrors the default TIERS list (highest tier → lowest). The display
# name preserves the casing rendered by the frontend.
_TIER_KEYS: list[tuple[str, str]] = [
    ("GOAT", "GOAT"),
    ("OG", "OG"),
    ("LEGEND", "Legend"),
    ("BASED", "Based"),
    ("DEGEN", "Degen"),
    ("NORMIE", "Normie"),
    ("ANON", "Anon"),
]


async def load_tiers_from_settings(db) -> bool:
    """Rebuild the module-level TIERS list from `TIER_*_THRESHOLD` /
    `TIER_*_MULTIPLIER` SiteSetting rows.

    Returns True if the rebuild happened, False if any key was missing/invalid
    (in which case the existing TIERS list is left untouched and a warning is
    logged). Safe to call repeatedly — e.g. from the lifespan startup and from
    the admin PUT endpoint after a TIER_* setting is changed.
    """
    # local import to avoid the module-import-time cycle (site_settings has no
    # cycle today, but tier.py is imported very early by other services).
    from app.services.site_settings import get_setting

    _SENTINEL = object()
    new_tiers: list[tuple[str, int, Decimal]] = []
    for key_suffix, display in _TIER_KEYS:
        # Anon is the catch-all — by definition its threshold is 0, so we
        # don't expect/require a TIER_ANON_THRESHOLD SiteSetting row. Only
        # the multiplier is configurable for Anon.
        is_anon = key_suffix == "ANON"
        thr_key = f"TIER_{key_suffix}_THRESHOLD"
        mul_key = f"TIER_{key_suffix}_MULTIPLIER"
        mul_raw = await get_setting(db, mul_key, default=_SENTINEL)
        if is_anon:
            thr_raw: object = 0
        else:
            thr_raw = await get_setting(db, thr_key, default=_SENTINEL)
        if thr_raw is _SENTINEL or mul_raw is _SENTINEL:
            missing = thr_key if thr_raw is _SENTINEL else mul_key
            logger.warning(
                "load_tiers_from_settings: missing %s — keeping hardcoded TIERS defaults",
                missing,
            )
            return False
        try:
            threshold = int(thr_raw)
            multiplier = mul_raw if isinstance(mul_raw, Decimal) else Decimal(str(mul_raw))
        except (TypeError, ValueError, InvalidOperation) as e:
            logger.warning(
                "load_tiers_from_settings: invalid value for %s/%s (%r/%r): %s — keeping defaults",
                thr_key, mul_key, thr_raw, mul_raw, e,
            )
            return False
        new_tiers.append((display, threshold, multiplier))

    # preserve highest-threshold-first ordering regardless of insertion order
    new_tiers.sort(key=lambda b: b[1], reverse=True)

    # in-place replacement so existing `from .tier import TIERS` references
    # see the updated bands without a re-import.
    TIERS.clear()
    TIERS.extend(new_tiers)
    logger.info("load_tiers_from_settings: rebuilt TIERS from DB (%d bands)", len(TIERS))
    return True


def _band(score: float) -> tuple[str, int, Decimal]:
    s = int(score or 0)
    for band in TIERS:
        if s >= band[1]:
            return band
    return TIERS[-1]  # Anon — unreachable since the last threshold is 0


def tier_for(score: float) -> str:
    """The tier name for a TweetScout score (e.g. 'Based')."""
    return _band(score)[0]


def multiplier_for(score: float) -> Decimal:
    """The karma multiplier for a TweetScout score (e.g. Decimal('1.20'))."""
    return _band(score)[2]


def karma_for(
    base: Decimal,
    score: float,
    streak_multiplier: Decimal | None = None,
) -> tuple[Decimal, Decimal]:
    """Return (karma, multiplier) for a base amount at a given score.

    karma = base × tier_mul × streak_mul, banker's-rounded to 4 dp. This
    exact amount is both deducted from escrow and credited to the engager —
    no inflation. The returned multiplier is the *combined* tier × streak
    multiplier (so callers can keep storing one number for the audit trail).

    ``streak_multiplier`` defaults to 1.0 (no boost) so the many call sites
    that don't care about streaks stay backward-compatible.
    """
    if not isinstance(base, Decimal):
        base = Decimal(str(base))
    tier_multiplier = multiplier_for(score)
    if streak_multiplier is None:
        streak_multiplier = Decimal("1")
    elif not isinstance(streak_multiplier, Decimal):
        streak_multiplier = Decimal(str(streak_multiplier))
    combined = (tier_multiplier * streak_multiplier).quantize(
        KARMA_QUANTIZE, rounding=ROUND_HALF_EVEN,
    )
    karma = (base * combined).quantize(KARMA_QUANTIZE, rounding=ROUND_HALF_EVEN)
    return karma, combined
