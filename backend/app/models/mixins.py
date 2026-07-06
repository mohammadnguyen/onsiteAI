"""Shared ORM mixins.

:class:`SoftDeleteMixin` adds a nullable ``deleted_at`` marker. A row
whose ``deleted_at`` is non-NULL is treated as deleted: a single global
``do_orm_execute`` listener (registered once in :mod:`app.database`)
transparently appends ``deleted_at IS NULL`` to every ORM ``SELECT`` that
targets a :class:`SoftDeleteMixin` subclass — including relationship
loads, via ``with_loader_criteria(propagate_to_loaders=True)``.

Two important scoping properties:

* **Only subclasses are affected.** ``with_loader_criteria`` restricts
  the added WHERE to entities of this mixin's type, so every other model
  (``Job`` / ``User`` / ``Expense`` / ``TimelineAuditLog`` / ...) is left
  completely untouched by the filter.
* **Escape hatch.** Pass ``execution_options(include_deleted=True)`` on a
  statement (or ``session.get(..., execution_options=...)``) to skip the
  filter for audit / admin / restore paths.

The audit log is intentionally *not* soft-deletable — it does not use
this mixin.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime
from sqlalchemy.orm import Mapped, mapped_column


class SoftDeleteMixin:
    """Adds a nullable ``deleted_at`` soft-delete marker column.

    The column definition matches the ``deleted_at`` created by the PR 1
    Timeline migrations exactly (``TIMESTAMPTZ NULL``), so adopting the
    mixin on those tables is a pure refactor — no schema change, no
    migration.
    """

    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
