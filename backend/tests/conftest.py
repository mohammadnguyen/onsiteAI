"""Test fixtures for Phase 1 Task 1.

Minimal harness: the only test so far (``test_health.py``) doesn't touch the
database, so we only need an in-process HTTPX client wired to the ASGI app.

A DB-backed fixture (disposable schema, factory-boy, seeded admin) lands with
Task 5's conftest expansion — not needed yet.
"""

import os

# Settings must have values before ``app.main`` (which imports ``app.config``)
# is loaded, so we set defaults here before the ``app.main`` import below.
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://sitetracker:sitetracker@localhost:5432/sitetracker_test",
)
os.environ.setdefault("JWT_SECRET", "test-secret")

import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest_asyncio.fixture
async def client():
    """Yield an ``AsyncClient`` bound to the FastAPI app via ASGITransport."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac
