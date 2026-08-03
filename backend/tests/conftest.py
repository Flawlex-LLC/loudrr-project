import uuid
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.db.base import Base
import app.models  # noqa: F401 — registers every table on Base.metadata
from app.models.user import User
from app.models.site_setting import SiteSetting
from app.services import site_settings

# Same Postgres server as the app (and the Django project), but a SEPARATE
# database so tests never touch real data. Create it once:
#   CREATE DATABASE loudrr_test;
TEST_DATABASE_URL = settings.database_url.rsplit("/", 1)[0] + "/loudrr_test"


async def _discover_pg_enum_types(conn) -> list[str]:
    """Return every ENUM type in the current DB's `public` schema.

    Base.metadata.drop_all() drops TABLES but NOT ENUM types (CREATE TYPE is
    a separate DDL verb at the pg_catalog level). Rather than hand-maintain
    a hardcoded list — which silently rots the moment anyone adds a new
    native ENUM column and starts flaking tests with "type X already exists"
    — we introspect pg_type at teardown time. Zero-maintenance: whatever
    lives in pg_catalog gets nuked, no matter what the ORM currently
    declares. `typtype='e'` filters to enum types only; schema='public'
    keeps us out of Postgres's built-ins.
    """
    result = await conn.execute(text(
        "SELECT t.typname FROM pg_type t "
        "JOIN pg_namespace n ON t.typnamespace = n.oid "
        "WHERE t.typtype='e' AND n.nspname='public'"
    ))
    return [row[0] for row in result]


@pytest.fixture(scope="session", autouse=True)
def _reset_test_schema_once():
    """Run ONCE at pytest session start: nuke any tables + types left over
    from a previous test run that was interrupted (Ctrl-C, crash, orphan
    process holding a CREATE TYPE in an uncommitted tx). Without this, the
    very first test's create_all hits "transactiontype already exists" or
    deadlocks against the orphan backend.

    Per-test async fixtures still drop_all + create_all to keep test
    isolation — this fixture's only job is to guarantee a clean SLATE
    before that loop starts, no matter what state the prior process left
    the DB in.

    The fixture is SYNC and spins up a private asyncio loop via
    `asyncio.run()` exactly for this DDL. We use a private loop on purpose:
    an async session-scoped fixture would share a session-wide loop with
    every test, and asyncpg connections are loop-bound — that triggers
    "attached to a different loop" runtime errors when function-scoped
    tests run on their own loops. `asyncio.run()` builds a fresh loop, runs
    the DDL, closes the loop — no shared state with the test loops.
    """
    import asyncio  # noqa: PLC0415 — local import keeps test-only deps out of module load

    async def _do_reset():
        from asyncpg import connect  # noqa: PLC0415

        url_for_asyncpg = TEST_DATABASE_URL.replace("+asyncpg", "")
        admin_url = url_for_asyncpg.rsplit("/", 1)[0] + "/postgres"
        # 1. Terminate any orphan backends on loudrr_test from the
        # maintenance `postgres` db (you can't kill backends on the DB
        # you're connected to).
        admin = await connect(admin_url)
        try:
            await admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname='loudrr_test' AND pid <> pg_backend_pid()"
            )
        finally:
            await admin.close()
        # 2. Reset the test DB schema so per-test create_all has a known-
        # empty starting state, no matter what prior runs left behind.
        conn = await connect(url_for_asyncpg)
        try:
            await conn.execute("DROP SCHEMA IF EXISTS public CASCADE")
            await conn.execute("CREATE SCHEMA public")
            await conn.execute("GRANT ALL ON SCHEMA public TO loudrr")
            await conn.execute("GRANT ALL ON SCHEMA public TO PUBLIC")
        finally:
            await conn.close()

    asyncio.run(_do_reset())
    yield
    # session teardown — nothing to do; the next pytest run will reset again


@pytest_asyncio.fixture
async def db_session():
    """One fresh, empty schema per test — total isolation.

    NullPool: every acquire opens a brand-new asyncpg connection, every
    release closes it. The default pool keeps connections alive across
    tests, and asyncpg caches pg_namespace / pg_type OIDs per connection —
    so a pooled connection from test N would hold the OLD oids when test
    N+1's CREATE TYPE / CREATE TABLE runs, leading to "transactiontype
    already exists" against the stale OID. NullPool kills the cache by
    killing the connection.
    """
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    # BEFORE the test: drop tables, drop ENUM types (drop_all leaves them),
    # then build every table fresh. ENUM names discovered from pg_type — no
    # hand-maintained list to drift out of sync with the ORM.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        for type_name in await _discover_pg_enum_types(conn):
            await conn.execute(text(f"DROP TYPE IF EXISTS {type_name} CASCADE"))
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as session:
        # the credit service reads DAILY_EARN_CAP from site_settings,
        # so seed it — earn() needs a cap to check against
        session.add(SiteSetting(
            key="DAILY_EARN_CAP", value="100", data_type="int",
        ))
        await session.commit()
        # the settings helper caches values for 5 minutes; clear it so
        # THIS row is read fresh, not a value cached by an earlier test
        site_settings._cache.clear()
        yield session  # the test runs here, with this session
    # AFTER the test: drop tables + ENUM types, close the connection pool
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        for type_name in await _discover_pg_enum_types(conn):
            await conn.execute(text(f"DROP TYPE IF EXISTS {type_name} CASCADE"))
    await engine.dispose()


@pytest.fixture
def make_user(db_session):
    """Factory: ``await make_user(credits=Decimal('50'))`` -> a saved User.
    Defaults give a valid zero-balance user, with unique values for the
    UNIQUE columns so two users in one test never collide."""
    async def _make(**overrides):
        values = dict(
            telegram_id=uuid.uuid4().int % 1_000_000_000,
            referral_code=uuid.uuid4().hex[:10].upper(),
            credits=Decimal("0"),
            total_credits_earned=Decimal("0"),
            total_credits_spent=Decimal("0"),
        )
        values.update(overrides)  # the test's overrides win
        user = User(**values)
        db_session.add(user)
        await db_session.commit()
        return user
    return _make


@pytest_asyncio.fixture
async def client(db_session):
    """An httpx client wired to the real app, but pointed at the test session
    and with rate-limiting off. Endpoints authenticate via ``?telegram_id=``
    (the debug bypass), since settings.debug is True in .env."""
    from app.main import app
    from app.db.session import get_session

    async def _use_test_session():
        yield db_session

    app.dependency_overrides[get_session] = _use_test_session
    app.state.limiter.enabled = False  # don't let 5/hour limits break tests
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
    app.state.limiter.enabled = True