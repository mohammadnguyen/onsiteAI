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
#
# Prod-readiness Slice 1 (ADR 0002): the env-aware loader treats ``test``
# as non-development, so the same fail-fast gates that protect production
# also fire during pytest. To keep the suite green we pre-seed:
#   * APP_ENV=test            — selects ``.env.test`` if present, else env vars only
#   * (pop ENVIRONMENT)       — avoid the APP_ENV/ENVIRONMENT conflict validator if
#                               a developer's shell has ENVIRONMENT set
#   * JWT_SECRET              — 51-char non-placeholder secret (length silences
#                               pyjwt's InsecureKeyLengthWarning and passes the
#                               non-dev length gate)
#   * CORS_ALLOWED_ORIGINS    — non-empty, non-wildcard; satisfies the non-dev
#                               origins gate without exposing real origins
os.environ.pop("ENVIRONMENT", None)
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://sitetracker:sitetracker@localhost:5433/sitetracker_test",
)
os.environ.setdefault("JWT_SECRET", "test-secret-for-sitetracker-phase1-never-production")
os.environ.setdefault("CORS_ALLOWED_ORIGINS", "https://localhost.test")
# Disable the auth rate limiter during the suite (audit E2): many tests share
# the same client IP + admin email and would otherwise trip a per-minute cap.
# The focused test in test_auth_rate_limit.py re-enables it explicitly.
os.environ.setdefault("AUTH_RATE_LIMIT_PER_MINUTE", "0")

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
        # Audit D-5: ``join_transaction_mode="create_savepoint"`` makes the
        # session operate inside a SAVEPOINT of the outer transaction. A
        # mid-test IntegrityError (duplicate key, CHECK violation) then rolls
        # back only the savepoint, leaving the outer transaction alive for the
        # teardown ``trans.rollback()`` — which previously warned "transaction
        # already deassociated from connection" because the failed statement
        # had aborted the whole DB transaction.
        session = AsyncSession(
            bind=conn,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )

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


@pytest_asyncio.fixture
async def seeded_contributor(db_session) -> User:
    """Insert a contributor user into the current (rolled-back) transaction.

    Used by RBAC tests (Task 6 onward) to assert that non-admin callers
    get a 403 on admin-only routes.
    """
    user = User(
        user_id=uuid.uuid4(),
        full_name="Seed Contributor",
        email="contributor@example.com",
        password_hash=hash_password("contributor"),
        role=UserRole.contributor,
        language_preference=LanguageCode.en,
        is_active=True,
    )
    db_session.add(user)
    await db_session.flush()
    return user


@pytest_asyncio.fixture
async def contributor_token(seeded_contributor) -> str:
    """Signed access token for the seeded contributor."""
    return create_access_token({"sub": str(seeded_contributor.user_id)})


@pytest_asyncio.fixture
async def seed_categories(db_session):
    """Populate the 23 builder categories within the current transaction."""
    from app.core.seed import seed_builder_categories

    return await seed_builder_categories(db_session)
