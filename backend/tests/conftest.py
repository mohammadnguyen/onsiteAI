"""Shared pytest fixtures for the backend test suite.

A DB-backed fixture stack is introduced here for Task 3 (User model). Each
test function gets an :class:`AsyncSession` bound to a transaction that is
rolled back at teardown, so tests don't leak state. The schema is built
directly from SQLAlchemy metadata against the ``sitetracker_test`` database
(faster than Alembic for test bootstrap; migrations are still exercised
separately in the upgrade/downgrade workflow).

The seeded admin-user fixture arrives with Task 5.
"""

import os

# Settings must have values before ``app.main`` (which imports ``app.config``)
# is loaded, so we set defaults here before the ``app.main`` import below.
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://sitetracker:sitetracker@localhost:5433/sitetracker_test",
)
os.environ.setdefault("JWT_SECRET", "test-secret")

import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine  # noqa: E402

from app.main import app  # noqa: E402
from app.models.base import Base  # noqa: E402
from app.models import user as _user_model  # noqa: E402, F401  # register User metadata

TEST_DB_URL = "postgresql+asyncpg://sitetracker:sitetracker@localhost:5433/sitetracker_test"


@pytest_asyncio.fixture
async def client():
    """Yield an ``AsyncClient`` bound to the FastAPI app via ASGITransport."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


@pytest_asyncio.fixture(scope="session")
async def _test_engine():
    """Session-scoped async engine for the dedicated ``sitetracker_test`` DB.

    The test database must already exist (created once per fresh Docker
    volume via ``CREATE DATABASE sitetracker_test OWNER sitetracker``). On
    first use of this fixture, all tables are dropped and re-created from
    :attr:`Base.metadata` so the schema always matches the current models.
    """
    engine = create_async_engine(TEST_DB_URL, future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(_test_engine):
    """Yield an ``AsyncSession`` wrapped in a transaction rolled back on teardown."""
    async with _test_engine.connect() as conn:
        trans = await conn.begin()
        session = AsyncSession(bind=conn, expire_on_commit=False)
        try:
            yield session
        finally:
            await session.close()
            await trans.rollback()
