"""Async SQLAlchemy engine, sessionmaker, and FastAPI dependency.

The engine and sessionmaker are constructed lazily so that tests can override
``DATABASE_URL`` (and then clear :func:`app.config.get_settings`'s cache)
before any connection is opened.
"""

from typing import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    """Return the process-wide async engine, creating it on first use."""
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            get_settings().database_url,
            echo=False,
            future=True,
        )
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Return the process-wide async sessionmaker, creating it on first use."""
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            get_engine(),
            expire_on_commit=False,
            class_=AsyncSession,
        )
    return _sessionmaker


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency that yields an ``AsyncSession`` per request."""
    async with get_sessionmaker()() as session:
        yield session
