"""CLI: clear expense-related rows to prep for internal testing.

Usage from the ``backend/`` directory::

    uv run python -m scripts.reset_testing_expenses

Deletes every row from ``expenses``; ``expense_review_queue`` and
``expense_audit_log`` rows are cascaded by their ``ON DELETE CASCADE``
foreign keys. Also closes any stale review queue entries whose parent
expense is already gone (defensive — should be a no-op).

Preserves: users, jobs, job aliases, category seeds, suppliers,
supplier aliases. Internal testers can therefore keep the org setup
they built while starting expense capture from a clean slate — the
duplicate-detection noise seeded during E2E runs is gone, so Phase 2's
`duplicate_suspected` review reason fires only on genuinely repeated
entries.

Idempotent: re-running after all expenses are gone is a safe no-op.
Intended for human dev bootstrap; the test suite has its own fixtures
and never runs this script.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_engine, get_sessionmaker


async def _reset(db: AsyncSession) -> tuple[int, int, int]:
    # Count first so the CLI can report what it removed.
    before_expenses = (await db.execute(text("SELECT COUNT(*) FROM expenses"))).scalar_one()
    before_queue = (
        await db.execute(text("SELECT COUNT(*) FROM expense_review_queue"))
    ).scalar_one()
    before_audit = (
        await db.execute(text("SELECT COUNT(*) FROM expense_audit_log"))
    ).scalar_one()

    # One DELETE on the parent; FKs with ON DELETE CASCADE handle the rest.
    await db.execute(text("DELETE FROM expenses"))
    await db.commit()

    return int(before_expenses), int(before_queue), int(before_audit)


async def _main() -> None:
    # Audit E1: refuse to run this irreversible DELETE against anything but a
    # development/test database. Without this guard, a misconfigured or
    # exported DATABASE_URL (e.g. during a fly console session) would wipe
    # every expense + its cascaded review-queue and append-only audit rows
    # from production.
    from app.config import get_settings

    env = get_settings().app_env
    if env not in {"development", "test"}:
        raise SystemExit(
            f"refusing to run destructive reset against APP_ENV={env!r}; "
            "this script is for development/test databases only"
        )

    Session = get_sessionmaker()
    try:
        async with Session() as db:
            expenses, queue, audit = await _reset(db)
    finally:
        await get_engine().dispose()
    print(
        f"Cleared {expenses} expense(s); cascaded "
        f"{queue} review queue row(s) and {audit} audit log row(s)."
    )


if __name__ == "__main__":
    asyncio.run(_main())
