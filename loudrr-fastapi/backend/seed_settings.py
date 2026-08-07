import asyncio
from sqlalchemy import select
from app.db.session import SessionLocal
from app.models.site_setting import SiteSetting

SETTINGS = [
    ("POST_COST", "80", "int", "Base karma cost to post"),
    ("POST_COST_MIN", "10", "int", "Minimum karma for a post"),
    ("POST_COST_MAX", "200", "int", "Maximum karma for a post"),
    ("CREDIT_PER_ENGAGEMENT", "1", "int", "Base credit per engagement"),
    ("DAILY_EARN_CAP", "160", "int", "Max karma earnable per day"),
]


async def seed():
    async with SessionLocal() as db:
        inserted = 0
        for key, value, data_type, description in SETTINGS:
            # check if it already exists
            result = await db.execute(select(SiteSetting).where(SiteSetting.key == key))
            if result.scalar_one_or_none() is not None:
                continue  # already seeded, skip
            db.add(
                SiteSetting(
                    key=key,
                    value=value,
                    data_type=data_type,
                    description=description,
                )
            )
            inserted += 1
        await db.commit()
    print(
        f"Seeded {inserted} new settings ({len(SETTINGS) - inserted} already existed)."
    )


asyncio.run(seed())
