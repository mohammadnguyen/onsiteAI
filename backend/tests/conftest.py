"""Shared pytest fixtures for the backend test suite.

A DB-backed fixture stack is introduced here for Task 3 (User model). Each
test function gets an :class:`AsyncSession` bound to a transaction that is
rolled back at teardown, so tests don't leak state. The schema is built
directly from SQLAlchemy metadata against the ``sitetracker_test`` database
(faster than Alembic for test bootstrap; migrations are still exercised
separately in the upgrade/downgrade workflow).

Task 5 adds a seeded admin user fixture and wires FastAPI's ``get_db``
dependency override so the ASGI ``client`` reads rows written via
``db_session`` — same session, same transaction, same rollback.
"""

import os

# Settings must have values before ``app.main`` (which imports ``app.config``)
# is loaded, so we set defaults here before the ``app.main`` import below.
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://sitetracker:sitetracker@localhost:5433/sitetracker_test",
)
# 32+ bytes silences pyjwt's InsecureKeyLengthWarning (HMAC-SHA256 wants >= 32).
os.environ.setdefault(
    "JWT_SECRET", "test-secret-for-sitetracker-phase1-never-production"
)

import uuid  # noqa: E402

import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine  # noqa: E402

from app.core.security import (  # noqa: E402
    create_access_token,
    create_refresh_token,
    hash_password,
)
from app.database import get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import user as _user_model  # noqa: E402, F401  # register User metadata
from app.models.base import Base  # noqa: E402
from app.models.user import LanguageCode, User, UserRole  # noqa: E402

TEST_DB_URL = "postgresql+asyncpg://sitetracker:sitetracker@localhost:5433/sitetracker_test"

SEED_ADMIN_EMAIL = "admin@example.com"
SEED_ADMIN_PASSWORD = "admin"


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
    """Yield an ``AsyncSession`` wrapped in a transaction rolled back on teardown.

    Also patches FastAPI's ``get_db`` dependency so any endpoint reached
    through the ``client`` fixture reads via the exact same session —
    i.e. sees rows the test has written but that have not yet been
    committed. The override is cleared on teardown.
    """
    async with _test_engine.connect() as conn:
        trans = await conn.begin()
        session = AsyncSession(bind=conn, expire_on_commit=False)

        async def _override_get_db():
            # Do NOT close the session here — the fixture owns its lifecycle.
            yield session

        app.dependency_overrides[get_db] = _override_get_db
        try:
            yield session
        finally:
            app.dependency_overrides.pop(get_db, None)
            await session.close()
            await trans.rollback()


@pytest_asyncio.fixture
async def client(db_session):
    """AsyncClient bound to the FastAPI app; ``get_db`` is overridden via ``db_session``.

    Tests that don't need DB access (e.g. ``/healthz``) still get this
    client — pulling ``db_session`` in for them is harmless: the
    transaction is opened and rolled back with nothing in it.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


@pytest_asyncio.fixture
async def seeded_admin(db_session) -> User:
    """Insert a single admin user into the current (rolled-back) transaction."""
    user = User(
        user_id=uuid.uuid4(),
        full_name="Seed Admin",
        email=SEED_ADMIN_EMAIL,
        password_hash=hash_password(SEED_ADMIN_PASSWORD),
        role=UserRole.admin,
        language_preference=LanguageCode.en,
        is_active=True,
    )
    db_session.add(user)
    await db_session.flush()
    return user


@pytest_asyncio.fixture
async def admin_token(seeded_admin) -> str:
    """Signed access token for the seeded admin (built in-process, no HTTP)."""
    return create_access_token({"sub": str(seeded_admin.user_id)})


@pytest_asyncio.fixture
async def admin_refresh_token(seeded_admin) -> str:
    """Signed refresh token for the seeded admin."""
    return create_refresh_token({"sub": str(seeded_admin.user_id)})
