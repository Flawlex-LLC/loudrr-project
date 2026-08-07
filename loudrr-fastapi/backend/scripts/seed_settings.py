"""Seed the SiteSetting rows the app expects at runtime.

Idempotent: upserts each key defined in `app.core.site_settings_meta.ALL_GROUPS`,
which is the single source of truth for defaults, types, descriptions, and
group sectioning. The metadata module covers Django's full ECHO_CONFIG plus
our FastAPI-specific keys (POST_COST_MIN/MAX bounds, etc.).

Skip if value/data_type already match; update otherwise; insert if missing.
Bust the in-process site-settings cache at the end.

Run from backend/ with:
    ../.venv/Scripts/python.exe -m scripts.seed_settings
"""
import asyncio
from collections import defaultdict

from sqlalchemy import select

from app.core.site_settings_meta import ALL_GROUPS
from app.db.session import SessionLocal
from app.models.site_setting import SiteSetting
from app.services import site_settings as site_settings_svc


async def seed() -> None:
    # key -> (created|updated|unchanged), key -> group name
    status: dict[str, str] = {}
    key_to_group: dict[str, str] = {}

    async with SessionLocal() as db:
        for group in ALL_GROUPS:
            for spec in group.settings:
                key_to_group[spec.key] = group.name
                row = (
                    await db.execute(
                        select(SiteSetting).where(SiteSetting.key == spec.key)
                    )
                ).scalar_one_or_none()
                if row is None:
                    db.add(SiteSetting(
                        key=spec.key,
                        value=spec.default,
                        data_type=spec.data_type,
                        description=spec.description,
                    ))
                    status[spec.key] = "created"
                elif row.value != spec.default or row.data_type != spec.data_type:
                    row.value = spec.default
                    row.data_type = spec.data_type
                    row.description = spec.description
                    status[spec.key] = "updated"
                else:
                    status[spec.key] = "unchanged"
        await db.commit()

    # Bust the in-process 5-minute cache so a long-running app sees the new values
    site_settings_svc._cache.clear()

    # Summary by group
    by_group: dict[str, dict[str, list[str]]] = defaultdict(
        lambda: {"created": [], "updated": [], "unchanged": []}
    )
    for key, st in status.items():
        by_group[key_to_group[key]][st].append(key)

    total = len(status)
    created_n = sum(1 for s in status.values() if s == "created")
    updated_n = sum(1 for s in status.values() if s == "updated")
    unchanged_n = sum(1 for s in status.values() if s == "unchanged")

    print(f"Seeded {total} settings across {len(ALL_GROUPS)} groups "
          f"({created_n} created, {updated_n} updated, {unchanged_n} unchanged)")
    for group in ALL_GROUPS:
        buckets = by_group[group.name]
        print(f"\n[{group.name}] — {group.description}")
        if buckets["created"]:
            print(f"  created:   {buckets['created']}")
        if buckets["updated"]:
            print(f"  updated:   {buckets['updated']}")
        if buckets["unchanged"]:
            print(f"  unchanged: {buckets['unchanged']}")

    print(f"\n  cache cleared — re-fetched on next request")
    print(f"  NOTE: a running uvicorn process has its own cache; restart it to pick up changes.")


if __name__ == "__main__":
    asyncio.run(seed())
