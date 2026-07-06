"""Async SQLAlchemy engine, sessionmaker, and FastAPI dependency.

The engine and sessionmaker are constructed lazily so that tests can override
``DATABASE_URL`` (and then clear :func:`app.config.get_settings`'s cache)
before any connection is opened.
"""

from collections.abc import AsyncIterator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import ORMExecuteState, Session, with_loader_criteria

from app.config import get_settings
from app.models.mixins import SoftDeleteMixin

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


# Global soft-delete filter. Registered on the base ``Session`` class (which
# every ``AsyncSession`` — app-created or test-created — wraps) so a single
# listener covers all query paths without touching the sessionmaker. The
# criteria is scoped to ``SoftDeleteMixin`` subclasses, so entities without
# the mixin (Job, User, Expense, TimelineAuditLog, ...) are unaffected.
#
# Guard rationale (verified against SQLAlchemy 2.0.49 ORMExecuteState):
#   * is_select + is_orm_statement — only ORM entity SELECTs; raw ``text()``
#     and Core-table statements are is_select/is_orm_statement False and are
#     left alone (no ``.options()`` is ever applied to them).
#   * not is_column_load — skip refresh / deferred / expired PK reloads; a
#     straight primary-key refetch must not gain a WHERE clause.
#   * not is_relationship_load — skip lazy/selectin/subquery loads; the
#     criteria already propagates into them via propagate_to_loaders=True
#     (default), so re-adding here would double it.
#   * not include_deleted — the execution-option escape hatch.
@event.listens_for(Session, "do_orm_execute")
def _filter_soft_deleted(execute_state: ORMExecuteState) -> None:
    """Append ``deleted_at IS NULL`` to ORM SELECTs on soft-deletable entities."""
    if (
        execute_state.is_select
        and execute_state.is_orm_statement
        and not execute_state.is_column_load
        and not execute_state.is_relationship_load
        and not execute_state.execution_options.get("include_deleted", False)
    ):
        execute_state.statement = execute_state.statement.options(
            with_loader_criteria(
                SoftDeleteMixin,
                lambda cls: cls.deleted_at.is_(None),
                include_aliases=True,
            )
        )


def get_engine() -> AsyncEngine:
    """Return the process-wide async engine, creating it on first use."""
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(
            settings.database_url,
            echo=(settings.environment == "development"),
            future=True,
            # Audit D-7: managed Postgres (Fly MPG) drops idle connections.
            # pool_pre_ping issues a cheap liveness check on checkout and
            # transparently reconnects a dead connection; pool_recycle caps
            # connection age below the provider's idle-kill window. Without
            # these, the first request after an idle period fails with a
            # connection-reset 500 — exactly the weak-network field scenario.
            pool_pre_ping=True,
            pool_recycle=1800,
        )
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Return the process-wide async sessionmaker, creating it on first use."""
    global _sessionmaker
    if _sessionmaker is None:
        # expire_on_commit=False prevents SQLAlchemy from re-issuing SELECTs on
        # attribute access after a commit, which would trigger IO on detached
        # instances outside the request-scoped session. Standard choice for
        # async FastAPI apps.
        _sessionmaker = async_sessionmaker(
            get_engine(),
            expire_on_commit=False,
            class_=AsyncSession,
        )
    return _sessionmaker


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency that yields an ``AsyncSession`` per request.

    Commits the session when the handler returns normally, and rolls back if
    the handler raises. Keeping the commit here (rather than in every
    handler) keeps the write semantics consistent across endpoints and
    matches the FastAPI-SQLAlchemy idiom. Tests replace this dependency via
    ``app.dependency_overrides[get_db]`` so the commit here never runs
    against the test database — test writes stay inside the fixture's
    rollback-on-teardown transaction.
    """
    async with get_sessionmaker()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
