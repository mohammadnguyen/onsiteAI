"""Public-facing labour schemas (Labour v1 L-A + Labour v2 L-C1).

Labour COST CAPTURE, never payroll. v2 adds an optional ``hourly_rate``
on workers and optional ``hours`` on entries; labour cost is computed
as ``hours * rate_snapshot`` on read and is NEVER stored as money. No
wages/salary/super/tax/overtime concepts exist here. ``hourly_rate``
and labour cost are admin-only (the route strips the rate for
non-admin callers); ``rate_snapshot`` is server-side only and never
appears in a public response.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_ALLOWED_FRACTIONS = (Decimal("0.5"), Decimal("1.0"))
_MAX_HOURS = Decimal("24")


class WorkerCreate(BaseModel):
    """Body of ``POST /workers`` (admin only)."""

    display_name: str = Field(min_length=1, max_length=120)
    note: str | None = Field(default=None, max_length=500)
    # v2: optional current hourly rate (admin sets it). >= 0.
    hourly_rate: Decimal | None = Field(default=None, ge=0)


class WorkerUpdate(BaseModel):
    """Body of ``PATCH /workers/{worker_id}`` (admin only).

    PATCH semantics mirror jobs: omitted field = no change; the route
    forwards ``model_dump(exclude_unset=True)``. ``note`` and
    ``hourly_rate`` may be set to explicit null to clear them.
    """

    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    note: str | None = Field(default=None, max_length=500)
    is_active: bool | None = None
    # v2: change the worker's current rate; explicit null clears it.
    # Changing this does NOT touch existing entries' rate_snapshot.
    hourly_rate: Decimal | None = Field(default=None, ge=0)


class WorkerPublic(BaseModel):
    """Serialised roster row.

    ``hourly_rate`` is ADMIN-ONLY: the ``GET /workers`` route nulls it
    for non-admin callers, so a contributor never sees pay rates.
    """

    model_config = ConfigDict(from_attributes=True)

    worker_id: uuid.UUID
    display_name: str
    note: str | None
    is_active: bool
    hourly_rate: Decimal | None = None


class LabourBatchItem(BaseModel):
    """One worker's tick within a batch save."""

    worker_id: uuid.UUID
    day_fraction: Decimal
    # v2: optional hours for this entry. None = no hours recorded (the
    # entry still counts for attendance; its cost is left incomplete).
    # v2.1 (L-C3): when start_time + end_time are both supplied the
    # service DERIVES hours from them and ignores any client ``hours``.
    hours: Decimal | None = None
    # v2.1 (L-C3): optional start/end TIME-OF-DAY. Both-or-neither (a lone
    # time is rejected below); when both are present the service derives
    # hours as the same-day span. The end-after-start ordering is enforced
    # in the service so a single rule owns it (with the DB CHECK as
    # backstop); this schema only guards the both-or-neither shape.
    start_time: time | None = None
    end_time: time | None = None

    @field_validator("day_fraction")
    @classmethod
    def _fraction_allowed(cls, v: Decimal) -> Decimal:
        if v not in _ALLOWED_FRACTIONS:
            raise ValueError("day_fraction must be 0.5 or 1.0")
        return v

    @field_validator("hours")
    @classmethod
    def _hours_in_range(cls, v: Decimal | None) -> Decimal | None:
        if v is not None and not (Decimal("0") < v <= _MAX_HOURS):
            raise ValueError("hours must be greater than 0 and at most 24")
        return v

    @model_validator(mode="after")
    def _times_both_or_neither(self) -> "LabourBatchItem":
        if (self.start_time is None) != (self.end_time is None):
            raise ValueError(
                "Provide both a start time and an end time, or neither"
            )
        return self


class LabourBatchRequest(BaseModel):
    """Body of ``POST /labour-entries/batch`` — the tick-screen save.

    All-or-nothing: any invalid row rejects the whole batch. Removals
    are deliberately NOT expressible here (no implicit deletes of
    other users' records) — unticking a worker is an explicit
    ``DELETE /labour-entries/{id}`` governed by the edit-permission
    rules.
    """

    job_id: uuid.UUID
    work_date: date
    entries: list[LabourBatchItem] = Field(min_length=1, max_length=50)


class LabourEntryPublic(BaseModel):
    """Serialised attendance entry."""

    model_config = ConfigDict(from_attributes=True)

    entry_id: uuid.UUID
    worker_id: uuid.UUID
    job_id: uuid.UUID
    work_date: date
    day_fraction: Decimal
    # v2: hours is exposed (any-auth — not sensitive). rate_snapshot is
    # deliberately NOT here — it stays server-side; cost surfaces only in
    # the admin-only summary.
    hours: Decimal | None = None
    # v2.1 (L-C3): the start/end times the hours were derived from (null
    # for hours-only or attendance-only entries). Exposed so the client
    # can redisplay and re-edit the range.
    start_time: time | None = None
    end_time: time | None = None
    recorded_by_user_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class WorkerDaysRow(BaseModel):
    """Per-worker totals within a summary range.

    ``total_days`` is worker-days (sum of day fractions) — kept for
    backward compatibility. v2 adds ``total_hours`` and ``labour_cost``
    (sum of ``hours * rate_snapshot``; null when no entry is costable).
    ``entries_total`` vs ``entries_costed`` lets the client flag an
    incomplete cost (some entries missing hours or rate).
    """

    worker_id: uuid.UUID
    display_name: str
    total_days: Decimal
    total_hours: Decimal | None = None
    labour_cost: Decimal | None = None
    entries_total: int = 0
    entries_costed: int = 0


class JobDaysRow(BaseModel):
    """Per-job totals within a summary range.

    ``total_days`` is worker-days (sum of day fractions) — the labour
    INPUT. ``days_on_site`` is the distinct count of dates anyone was on
    the job — the job's DURATION. Showing both fixes the misleading
    "4 workers x 1 day = 4 days" reading. v2 also adds hours + cost.
    """

    job_id: uuid.UUID
    job_name: str
    total_days: Decimal
    days_on_site: int = 0
    total_hours: Decimal | None = None
    labour_cost: Decimal | None = None
    entries_total: int = 0
    entries_costed: int = 0


class LabourSummary(BaseModel):
    """Response of ``GET /labour-summary`` (admin only).

    One payload serves the weekly labour summary (per worker) and the
    per-job labour-cost view. ``total_days`` is the grand worker-day
    total; v2 adds grand ``total_hours`` and ``total_labour_cost`` plus
    the costed/total entry counts for completeness signalling.
    """

    workers: list[WorkerDaysRow]
    jobs: list[JobDaysRow]
    total_days: Decimal
    total_hours: Decimal | None = None
    total_labour_cost: Decimal | None = None
    entries_total: int = 0
    entries_costed: int = 0
