"""Worker roster + labour attendance models (Labour v1 L-A + v2 L-C1).

Labour tracks ATTENDANCE IN DAYS (v1: who was on which site on which
date, full or half day) plus, in v2, optional HOURS per entry and a
per-worker HOURLY RATE for COST CAPTURE. It is a cost-capture aid, NOT
payroll: no wages, salary, super, tax, or overtime concepts. Labour
cost (hours × the rate snapshotted at entry creation) is computed on
read and NEVER stored as money; the external payroll system stays
authoritative for actual pay. Wage payments remain ordinary expenses
under the Labour category.

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
from datetime import time as time_type
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
    Time,
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
    # Labour v2 (slice L-C1): a worker's current default hourly rate, used
    # ONLY to snapshot onto new labour entries (see LabourEntry.rate_snapshot).
    # Nullable — a worker may have no rate yet, in which case their entries
    # carry no cost. Admin-managed; never exposed to non-admin callers.
    # This is a cost-CAPTURE aid, NOT payroll — no wages/super/tax concepts.
    hourly_rate: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=False
    )

    __table_args__ = (
        CheckConstraint("hourly_rate >= 0", name="ck_workers_hourly_rate"),
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
    # Labour v2 (slice L-C1): optional hours worked for this entry. Days
    # (day_fraction) remain the attendance record; hours add precision for
    # the cost guide. Independent of day_fraction — no cross-validation.
    hours: Mapped[Decimal | None] = mapped_column(Numeric(4, 2), nullable=True)
    # Labour v2: the worker's hourly_rate SNAPSHOTTED when this entry was
    # CREATED — write-once. Later changes to the worker's rate do NOT alter
    # past entries (a current rate may not reflect the historical rate). A
    # NULL snapshot (worker had no rate at create) stays NULL; filling it is
    # a deferred explicit-admin correction, never automatic. Labour cost =
    # hours * rate_snapshot, computed on read, never stored as money.
    rate_snapshot: Mapped[Decimal | None] = mapped_column(
        Numeric(8, 2), nullable=True
    )
    # Labour v2.1 (slice L-C3): optional start/end TIME-OF-DAY for this
    # entry. When BOTH are set the service DERIVES ``hours`` as the full
    # span (end - start, no break deduction) — the time range is then the
    # single source of truth and any client-sent ``hours`` is ignored.
    # SAME-DAY ONLY: the CHECK requires start < end; these are TIME values,
    # not timestamps, so overnight spans are out of scope. Both-or-neither
    # is enforced in the schema/service; a lone time is rejected.
    start_time: Mapped[time_type | None] = mapped_column(Time, nullable=True)
    end_time: Mapped[time_type | None] = mapped_column(Time, nullable=True)
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
        CheckConstraint(
            "hours > 0 AND hours <= 24",
            name="ck_labour_entries_hours",
        ),
        CheckConstraint(
            "rate_snapshot >= 0",
            name="ck_labour_entries_rate_snapshot",
        ),
        # L-C3: same-day ordering backstop. Either time may be null
        # (hours-only entries), but when both are present start must
        # precede end. The service rejects this earlier with a 422; this
        # CHECK is the DB-level defence against any other write path.
        CheckConstraint(
            "start_time IS NULL OR end_time IS NULL OR start_time < end_time",
            name="ck_labour_entries_time_order",
        ),
        # Per-job history / day totals.
        Index("ix_labour_entries_job_date", "job_id", "work_date"),
        # Per-worker summaries + the <=1.0 allocation lock lookup.
        Index("ix_labour_entries_worker_date", "worker_id", "work_date"),
    )
