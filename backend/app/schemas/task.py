"""Pydantic schemas for the Task API (Site Log → Task, plan v2).

No response model carries the proposed title (D3): it exists only in the
audit, which no endpoint returns. Tests pin the exact key sets.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.job import JobStatus
from app.models.task import TaskOrigin, TaskStatus


class TaskCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    create_client_id: uuid.UUID
    # Trimmed and length-checked by the service, which answers 422 with a
    # plain message; the bound here only stops absurd payloads early.
    title: str = Field(max_length=2000)
    assignee_user_id: uuid.UUID | None = None
    due_date: date | None = None


class TaskPatchIn(BaseModel):
    """Only the fields actually sent are changed (``model_fields_set``).

    ``job_id`` may be set or changed but never cleared; changing an
    existing job needs ``reason``.
    """

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=2000)
    status: TaskStatus | None = None
    assignee_user_id: uuid.UUID | None = None
    due_date: date | None = None
    job_id: uuid.UUID | None = None
    reason: str | None = Field(default=None, max_length=500)


class TaskUserOut(BaseModel):
    user_id: uuid.UUID
    full_name: str


class TaskJobOut(BaseModel):
    """Job identity only — no money."""

    job_id: uuid.UUID
    job_name: str
    job_code: str | None
    status: JobStatus


class TaskSourceOut(BaseModel):
    site_log_event_id: uuid.UUID
    revision_no: int
    # False: the caller may not open the entry; the app says so.
    readable: bool
    # The entry's current job — only when ``readable``, else null.
    job_id: uuid.UUID | None


class TaskPermissionsOut(BaseModel):
    change_status: bool
    edit: bool
    assign_others: bool
    set_job: bool
    change_job: bool


class TaskOut(BaseModel):
    task_id: uuid.UUID
    title: str
    status: TaskStatus
    origin: TaskOrigin
    due_date: date | None
    job: TaskJobOut | None
    assignee: TaskUserOut | None
    created_by: TaskUserOut | None
    source: TaskSourceOut
    permissions: TaskPermissionsOut
    created_at: datetime
    updated_at: datetime
