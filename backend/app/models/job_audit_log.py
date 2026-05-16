"""Job audit log model for Job Lifecycle v1A-1.

Append-only log of edits and lifecycle events on a :class:`~app.models
.job.Job`. Mirrors the established :class:`~app.models.review_queue
.ExpenseAuditLog` pattern with v1A-1-specific shape additions:

* ``tenant_id`` — forward-compat for multi-tenant. V1 is single-tenant
  so the DB ``server_default`` populates this for every row without
  the application setting it explicitly. When multi-tenant ships, a
  follow-up migration drops the server_default and the application
  begins setting it from the request context.
* ``job_id`` nullable + ``ON DELETE SET NULL`` — keeps the audit row
  queryable after the future hard-delete-empty-job action (v1A-3).
* ``job_name_snapshot`` / ``job_code_snapshot`` — preserve the
  human-meaningful identifier at the time of the event, so the audit
  trail remains readable after the parent job is gone.
* ``action`` — one of ``"edit"`` / ``"archive"`` / ``"reopen"`` /
  ``"delete"``. Application-validated; no DB ``CHECK`` so v1A-3 can
  add ``"delete"`` without a follow-up migration.

Convention: ``job_name_snapshot`` and ``job_code_snapshot`` reflect
the **pre-edit** state of the job, so rename audit rows show the OLD
name in the snapshot and the diff in ``changed_fields`` says what it
became. This keeps the snapshot a stable anchor: "this audit row was
about a job called X" remains true even after X is renamed or
deleted.

The model is registered in :mod:`app.models.__init__` and
:mod:`alembic.env` so SQLAlchemy ``Base.metadata.create_all`` (used
by the pytest fixture) and Alembic autogenerate both see it.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    UUID,
    DateTime,
    ForeignKey,
    Index,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.job import Job
    from app.models.user import User


class JobAuditLog(Base):
    """Append-only audit row for a job edit or lifecycle event."""

    __tablename__ = "job_audit_log"

    audit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # V1 single-tenant default. The DB server_default populates this
    # column for every audit row without the application setting it
    # explicitly. When multi-tenant support ships, the application
    # will set tenant_id from request context and a follow-up
    # migration drops the server_default.
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        server_default=text("'00000000-0000-0000-0000-000000000001'::uuid"),
    )

    # Nullable so audit history survives the future hard-delete-empty-
    # job action (v1A-3). SET NULL on FK cascade preserves the row;
    # the snapshot columns retain the human-meaningful identifier.
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jobs.job_id", ondelete="SET NULL"),
        nullable=True,
    )

    # Pre-edit snapshots. Always populated.
    job_name_snapshot: Mapped[str] = mapped_column(String(255), nullable=False)
    job_code_snapshot: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )

    # V1 has no user-deletion path; if a user ever did get deleted,
    # the default NO ACTION cascade rejects the delete via FK
    # constraint — surfacing as an IntegrityError at the application
    # boundary. Intended: we never want orphan audit rows.
    actor_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.user_id"),
        nullable=False,
    )

    # "edit" | "archive" | "reopen" | "delete" — application-validated.
    action: Mapped[str] = mapped_column(String(32), nullable=False)

    # Field diff: {field: {"old": coerced_value, "new": coerced_value}}.
    # Coercion handled by :func:`_coerce_job_audit_value` in
    # :mod:`app.services.jobs` (kept local to the jobs service to
    # avoid a cross-module dependency on the private helper in
    # :mod:`app.services.expenses`).
    changed_fields: Mapped[dict] = mapped_column(JSONB, nullable=False)

    # ``clock_timestamp()`` (NOT ``now()`` / ``func.now()``) so each
    # INSERT in a single transaction gets a distinct real-time value.
    # ``now()`` returns the transaction-start timestamp which collapses
    # multiple rapid audit inserts (e.g. several test PATCHes) to the
    # same timestamp, breaking ``ORDER BY created_at DESC``.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("clock_timestamp()"),
        nullable=False,
    )

    actor: Mapped["User"] = relationship(
        lazy="joined", foreign_keys=[actor_user_id]
    )
    job: Mapped["Job | None"] = relationship(
        lazy="select", foreign_keys=[job_id]
    )

    # Composite index supports two access patterns:
    # 1. Per-job audit trail (WHERE tenant_id = X AND job_id = Y
    #    ORDER BY created_at DESC) — leading tenant_id + job_id.
    # 2. Forward-compat tenant-wide audit (WHERE tenant_id = X
    #    ORDER BY created_at DESC) — uses the same index as a prefix.
    __table_args__ = (
        Index(
            "ix_job_audit_log_tenant_job_created",
            "tenant_id",
            "job_id",
            "created_at",
        ),
    )
