"""load_tiers_from_settings — DB-backed override of the hardcoded TIERS list.

Covers the fallback paths (missing / invalid values → keep defaults + log a
warning). Real production edge cases: partial migration, admin fat-fingered
a TIER_*_MULTIPLIER, admin deleted a TIER_* row. Coverage-motivated: without
these, tier.py sits at 45% coverage — the lowest of any service module.

Each test SNAPSHOTS the module-level TIERS list before touching it and
restores it in the finally block — otherwise a mid-test failure leaves TIERS
in a corrupted state that breaks every downstream test.
"""
import logging
from decimal import Decimal

from sqlalchemy import delete

from app.models.site_setting import SiteSetting
from app.services import site_settings
from app.services import tier


async def _clear_all_tier_settings(db_session):
    """Purge every TIER_*_THRESHOLD / TIER_*_MULTIPLIER row so we start from a
    known-empty slate."""
    await db_session.execute(
        delete(SiteSetting).where(SiteSetting.key.like(r"TIER\_%\_THRESHOLD"))
    )
    await db_session.execute(
        delete(SiteSetting).where(SiteSetting.key.like(r"TIER\_%\_MULTIPLIER"))
    )
    await db_session.commit()
    site_settings._cache.clear()


async def _seed_full_tier_settings(db_session, **overrides):
    """Seed the complete set of TIER_* settings (6 non-anon thresholds + 7
    multipliers) with plausible-but-different-from-defaults values so a
    successful load actually changes TIERS visibly."""
    values = {
        "TIER_GOAT_THRESHOLD":   ("1200", "int"),
        "TIER_OG_THRESHOLD":     ("900",  "int"),
        "TIER_LEGEND_THRESHOLD": ("700",  "int"),
        "TIER_BASED_THRESHOLD":  ("500",  "int"),
        "TIER_DEGEN_THRESHOLD":  ("300",  "int"),
        "TIER_NORMIE_THRESHOLD": ("150",  "int"),
        "TIER_GOAT_MULTIPLIER":   ("1.40", "decimal"),
        "TIER_OG_MULTIPLIER":     ("1.32", "decimal"),
        "TIER_LEGEND_MULTIPLIER": ("1.27", "decimal"),
        "TIER_BASED_MULTIPLIER":  ("1.22", "decimal"),
        "TIER_DEGEN_MULTIPLIER":  ("1.17", "decimal"),
        "TIER_NORMIE_MULTIPLIER": ("1.12", "decimal"),
        "TIER_ANON_MULTIPLIER":   ("1.01", "decimal"),
    }
    for k, v in overrides.items():
        dt = "int" if k.endswith("_THRESHOLD") else "decimal"
        values[k] = (v, dt)
    for k, (v, dt) in values.items():
        db_session.add(SiteSetting(key=k, value=v, data_type=dt))
    await db_session.commit()
    site_settings._cache.clear()


async def test_load_tiers_full_override_replaces_hardcoded_bands(db_session):
    """Every TIER_* setting present + valid → TIERS is rebuilt in place and
    future tier_for() calls see the new thresholds — no re-import needed."""
    saved = list(tier.TIERS)
    try:
        await _clear_all_tier_settings(db_session)
        await _seed_full_tier_settings(db_session)  # GOAT=1200 in these values

        result = await tier.load_tiers_from_settings(db_session)
        assert result is True
        # 1000 was GOAT under defaults, now maps to OG (900 <= 1000 < 1200)
        assert tier.tier_for(1000) == "OG"
        assert tier.tier_for(1200) == "GOAT"
        assert tier.multiplier_for(1200) == Decimal("1.40")
        # ordering invariant preserved
        thresholds = [t[1] for t in tier.TIERS]
        assert thresholds == sorted(thresholds, reverse=True)
    finally:
        tier.TIERS.clear()
        tier.TIERS.extend(saved)


async def test_load_tiers_missing_threshold_returns_false_and_keeps_defaults(
    db_session, caplog
):
    """A missing TIER_*_THRESHOLD aborts the rebuild — returns False, logs
    a warning naming the missing key, LEAVES existing TIERS untouched.
    Critical: a partial migration must never zero out the tier system."""
    saved = list(tier.TIERS)
    try:
        await _clear_all_tier_settings(db_session)
        # Full multiplier set, but OG threshold intentionally missing
        for k, v in [
            ("TIER_GOAT_THRESHOLD", "1200"),
            ("TIER_LEGEND_THRESHOLD", "700"),
            ("TIER_BASED_THRESHOLD", "500"),
            ("TIER_DEGEN_THRESHOLD", "300"),
            ("TIER_NORMIE_THRESHOLD", "150"),
        ]:
            db_session.add(SiteSetting(key=k, value=v, data_type="int"))
        for k in ("GOAT", "OG", "LEGEND", "BASED", "DEGEN", "NORMIE", "ANON"):
            db_session.add(SiteSetting(
                key=f"TIER_{k}_MULTIPLIER", value="1.25", data_type="decimal",
            ))
        await db_session.commit()
        site_settings._cache.clear()

        with caplog.at_level(logging.WARNING, logger="app.services.tier"):
            result = await tier.load_tiers_from_settings(db_session)
        assert result is False
        assert any("TIER_OG_THRESHOLD" in r.message for r in caplog.records)
        # TIERS untouched — GOAT threshold still 1000 (hardcoded default)
        assert tier.TIERS == saved
    finally:
        tier.TIERS.clear()
        tier.TIERS.extend(saved)


async def test_load_tiers_missing_multiplier_returns_false(db_session, caplog):
    """Missing MULTIPLIER is equally disqualifying — same fallback path."""
    saved = list(tier.TIERS)
    try:
        await _clear_all_tier_settings(db_session)
        for k, v in [
            ("TIER_GOAT_THRESHOLD", "1200"), ("TIER_OG_THRESHOLD", "900"),
            ("TIER_LEGEND_THRESHOLD", "700"), ("TIER_BASED_THRESHOLD", "500"),
            ("TIER_DEGEN_THRESHOLD", "300"), ("TIER_NORMIE_THRESHOLD", "150"),
        ]:
            db_session.add(SiteSetting(key=k, value=v, data_type="int"))
        # Multipliers except TIER_BASED_MULTIPLIER
        for k in ("GOAT", "OG", "LEGEND", "DEGEN", "NORMIE", "ANON"):
            db_session.add(SiteSetting(
                key=f"TIER_{k}_MULTIPLIER", value="1.25", data_type="decimal",
            ))
        await db_session.commit()
        site_settings._cache.clear()

        with caplog.at_level(logging.WARNING, logger="app.services.tier"):
            result = await tier.load_tiers_from_settings(db_session)
        assert result is False
        assert any("TIER_BASED_MULTIPLIER" in r.message for r in caplog.records)
        assert tier.TIERS == saved
    finally:
        tier.TIERS.clear()
        tier.TIERS.extend(saved)


async def test_load_tiers_invalid_threshold_type_returns_false(
    db_session, caplog
):
    """A non-int TIER_*_THRESHOLD (admin typo, stored as data_type='str' so
    the SiteSettings layer doesn't coerce) triggers the ValueError catch
    inside load_tiers_from_settings — same fallback: False + warn + keep
    defaults. Note the site_settings coercion via `data_type='int'` would
    reject the bad value earlier, so we test the tier-level defense-in-depth
    by using data_type='str' — the exact path the code guards against."""
    saved = list(tier.TIERS)
    try:
        await _clear_all_tier_settings(db_session)
        # Seed everything valid except GOAT threshold with a str value
        await _seed_full_tier_settings(db_session)
        # Overwrite GOAT threshold with a garbage str value
        from sqlalchemy import update
        await db_session.execute(
            update(SiteSetting)
            .where(SiteSetting.key == "TIER_GOAT_THRESHOLD")
            .values(value="not-a-number", data_type="str")
        )
        await db_session.commit()
        site_settings._cache.clear()

        with caplog.at_level(logging.WARNING, logger="app.services.tier"):
            result = await tier.load_tiers_from_settings(db_session)
        assert result is False
        assert any(
            "TIER_GOAT_THRESHOLD" in r.message or "TIER_GOAT_MULTIPLIER" in r.message
            for r in caplog.records
        )
        assert tier.TIERS == saved
    finally:
        tier.TIERS.clear()
        tier.TIERS.extend(saved)


async def test_load_tiers_out_of_order_settings_get_sorted_descending(db_session):
    """The service must ALWAYS produce a highest-threshold-first list even
    if the input rows are in random order — otherwise _band() top-down scan
    returns the wrong tier for scores between two bands."""
    saved = list(tier.TIERS)
    try:
        await _clear_all_tier_settings(db_session)
        await _seed_full_tier_settings(
            db_session,
            TIER_NORMIE_THRESHOLD="50",
            TIER_DEGEN_THRESHOLD="150",
            TIER_BASED_THRESHOLD="300",
        )
        result = await tier.load_tiers_from_settings(db_session)
        assert result is True
        thresholds = [t[1] for t in tier.TIERS]
        assert thresholds == sorted(thresholds, reverse=True)
        assert thresholds[-1] == 0  # Anon always last
        # correctness spot-checks with the overridden bands
        assert tier.tier_for(50) == "Normie"
        assert tier.tier_for(300) == "Based"
    finally:
        tier.TIERS.clear()
        tier.TIERS.extend(saved)


async def test_load_tiers_anon_threshold_is_forced_zero_multiplier_still_configurable(
    db_session,
):
    """Anon's threshold is hardcoded to 0 (spec §5.3 — catch-all), but its
    MULTIPLIER remains configurable. Even if someone inserted a bogus
    TIER_ANON_THRESHOLD row, the load function ignores it."""
    saved = list(tier.TIERS)
    try:
        await _clear_all_tier_settings(db_session)
        await _seed_full_tier_settings(db_session, TIER_ANON_MULTIPLIER="1.05")
        # Add a garbage TIER_ANON_THRESHOLD that should be ignored anyway
        db_session.add(SiteSetting(
            key="TIER_ANON_THRESHOLD", value="9999", data_type="int",
        ))
        await db_session.commit()
        site_settings._cache.clear()

        result = await tier.load_tiers_from_settings(db_session)
        assert result is True
        # Anon band still at 0 — the 9999 in the DB is not read
        assert tier.TIERS[-1][:2] == ("Anon", 0)
        assert tier.multiplier_for(0) == Decimal("1.05")
    finally:
        tier.TIERS.clear()
        tier.TIERS.extend(saved)
