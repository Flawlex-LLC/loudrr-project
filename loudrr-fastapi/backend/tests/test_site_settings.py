"""Unit tests for the cached site-settings reader."""
import pytest
from sqlalchemy import select

from app.models.site_setting import SiteSetting
from app.services import site_settings
from app.services.site_settings import get_setting


async def test_get_setting_reads_int(db_session):
    # DAILY_EARN_CAP=100 is seeded by the db_session fixture, typed "int"
    assert await get_setting(db_session, "DAILY_EARN_CAP") == 100


async def test_get_setting_missing_raises(db_session):
    with pytest.raises(KeyError):
        await get_setting(db_session, "DOES_NOT_EXIST")


async def test_get_setting_string_type_not_cast(db_session):
    db_session.add(SiteSetting(key="WELCOME", value="hello", data_type="str"))
    await db_session.commit()
    site_settings._cache.clear()
    assert await get_setting(db_session, "WELCOME") == "hello"


async def test_get_setting_is_cached(db_session):
    db_session.add(SiteSetting(key="FOO", value="5", data_type="int"))
    await db_session.commit()
    site_settings._cache.clear()
    assert await get_setting(db_session, "FOO") == 5      # reads DB + caches

    # change the row underneath the cache
    row = (await db_session.execute(
        select(SiteSetting).where(SiteSetting.key == "FOO")
    )).scalar_one()
    row.value = "9"
    await db_session.commit()

    assert await get_setting(db_session, "FOO") == 5      # still the cached value
    site_settings._cache.clear()
    assert await get_setting(db_session, "FOO") == 9      # fresh read after clear