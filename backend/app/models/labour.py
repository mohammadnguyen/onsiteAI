"""Worker roster + labour attendance models (Labour v1, slice L-A).

Labour v1 tracks ATTENDANCE IN DAYS — who was on which site on which
date, full or half day. It deliberately carries NO payroll concepts:
no rates, wages, hours, overtime, super, or tax. Wage payments remain
ordinary expenses under the Labour category; this module only answers
"who went where, for how many days".

A :class:`Worker` is a ROSTER RECORD, not an app user: workers never
log in, have no credentials, and are unrelated to ``users``.
``display_name`` is a label, NOT an identity — duplicates are allowed
by design (two "Li"s disambiguate via ``note``). Workers are
deactivated, never deleted: no delete path exists in the API, and
``labour_entries.worker_id`` is ``ON DELETE RESTRICT`` as the DB-level
backstop.

A :class:`LabourEntry` is one worker-day-fraction on one job.
``day_fraction`` ∈ {0.5, 1.0} (DB CHECK). UNIQUE (worker, job, date)
means re-recording the same worker/job/day UPDATES the fraction rather
than duplicating. The cross-row rule — a worker's TOTAL allocation per
date across ALL jobs may not exceed 1.0 — cannot be a CHECK constraint;
it is enforced in :mod:`app.services.labour` inside the write
transaction under row locks (explicit service logic over hidden
triggers, per house rules).

Audit-forward: ``recorded_by_user_id`` + the TimestampMixin pair are
the v1 audit minimum; a future ``labour_audit_log`` can reference
``entry_id`` additively without schema rework.
"""

from __future__ import annotations

import uuid
from datetime import date as date_type
from decimal import Decimal

from sqlalchemy import (
    UUID,
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class Worker(Base, TimestampMixin):
    """A rostered site worker (a record, never an app user)."""

    __tablename__ = "workers"

    worker_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=False
    )


class LabourEntry(Base, TimestampMixin):
    """One worker's attendance on one job for one date (0.5 or 1.0 days)."""

    __tablename__ = "labour_entries"

    entry_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    worker_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workers.worker_id", ondelete="RESTRICT"),
        nullable=False,
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jobs.job_id", ondelete="RESTRICT"),
        nullable=False,
    )
    work_date: Mapped[date_type] = mapped_column(Date, nullable=False)
    day_fraction: Mapped[Decimal] = mapped_column(Numeric(2, 1), nullable=False)
    recorded_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "worker_id",
            "job_id",
            "work_date",
            name="uq_labour_entries_worker_job_date",
        ),
        CheckConstraint(
            "day_fraction IN (0.5, 1.0)",
            name="ck_labour_entries_day_fraction",
        ),
        # Per-job history / day totals.
        Index("ix_labour_entries_job_date", "job_id", "work_date"),
        # Per-worker summaries + the <=1.0 allocation lock lookup.
        Index("ix_labour_entries_worker_date", "worker_id", "work_date"),
    )
