"""Public-facing labour schemas (Labour v1, slice L-A).

Attendance/days language ONLY — these shapes carry no payroll
concepts. The summary endpoint powers the mobile "fortnight
attendance summary" and per-job labour-days displays.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

_ALLOWED_FRACTIONS = (Decimal("0.5"), Decimal("1.0"))


class WorkerCreate(BaseModel):
    """Body of ``POST /workers`` (admin only)."""

    display_name: str = Field(min_length=1, max_length=120)
    note: str | None = Field(default=None, max_length=500)


class WorkerUpdate(BaseModel):
    """Body of ``PATCH /workers/{worker_id}`` (admin only).

    PATCH semantics mirror jobs: omitted field = no change; the route
    forwards ``model_dump(exclude_unset=True)``. ``note`` may be set
    to explicit null to clear it.
    """

    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    note: str | None = Field(default=None, max_length=500)
    is_active: bool | None = None


class WorkerPublic(BaseModel):
    """Serialised roster row."""

    model_config = ConfigDict(from_attributes=True)

    worker_id: uuid.UUID
    display_name: str
    note: str | None
    is_active: bool


class LabourBatchItem(BaseModel):
    """One worker's tick within a batch save."""

    worker_id: uuid.UUID
    day_fraction: Decimal

    @field_validator("day_fraction")
    @classmethod
    def _fraction_allowed(cls, v: Decimal) -> Decimal:
        if v not in _ALLOWED_FRACTIONS:
            raise ValueError("day_fraction must be 0.5 or 1.0")
        return v


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
    recorded_by_user_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class WorkerDaysRow(BaseModel):
    """Per-worker day total within a summary range."""

    worker_id: uuid.UUID
    display_name: str
    total_days: Decimal


class JobDaysRow(BaseModel):
    """Per-job day total within a summary range."""

    job_id: uuid.UUID
    job_name: str
    total_days: Decimal


class LabourSummary(BaseModel):
    """Response of ``GET /labour-summary`` (admin only).

    One payload serves both the fortnight attendance summary (per
    worker) and the per-job labour-days view. ``total_days`` is the
    grand total for the filtered range.
    """

    workers: list[WorkerDaysRow]
    jobs: list[JobDaysRow]
    total_days: Decimal
