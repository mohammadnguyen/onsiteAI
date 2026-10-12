"""Tasks made from Site Log entries (Site Log → Task, plan v2).

Two tables, both additive:

* ``tasks`` — one follow-up item, created by an explicit human action from
  one Site Log entry (DEC-TASK-001; Truth written by explicit human action,
  DEC-TRUTH-001). The entry is never modified by anything here: the task
  carries its own copy of what it needs (title, job) and a reference to the
  entry revision it was made from.
* ``task_audit_log`` — append-only history. The ``created`` row is the
  complete record of the human confirmation (D2): evidence ref (source
  entry + revision), the server-derived proposed title, whether it was
  edited, the final values, who and when. Later rows record one changed
  field each.

Boundary notes:

* ``job_id`` is the TASK's job. It is copied from the entry at creation and
  never follows a later assign or re-link of the entry (plan v2 §3a): one
  entry correction must not move several in-progress tasks. Changing it is
  an explicit, audited action.
* The audit is NOT content-free the way ``site_log_event_audit_log`` is: it
  holds titles by design (D3). It must never hold the entry's body text —
  :func:`validate_task_audit_detail` is the writer-side guard. The proposed
  title is derived from the entry, so it is readable only by callers who
  may read that entry; v1 exposes no audit endpoint at all.
* No AI candidate or confirmation tables exist (D2). ``origin`` records how
  a task came to be; a confirmed AI candidate would add its own value in its
  own migration and write through the same create service.
"""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy import Enum as SqlaEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

# Single-tenant default shared with the Site Log tables.
_TENANT_DEFAULT = text("'00000000-0000-0000-0000-000000000001'::uuid")

TITLE_MAX_CHARS = 200


class TaskStatus(str, enum.Enum):
    """The four statuses of DEC-TASK-001. Any status may move to any other."""

    to_do = "to_do"
    in_progress = "in_progress"
    blocked = "blocked"
    done = "done"


class TaskOrigin(str, enum.Enum):
    """How the task came to exist. v1 has one way: a person made it."""

    manual = "manual"


class TaskAuditAction(str, enum.Enum):
    """Application-validated audit vocabulary (String column, like the
    job and Site Log audit precedents)."""

    created = "created"
    title_changed = "title_changed"
    status_changed = "status_changed"
    assignee_changed = "assignee_changed"
    due_date_changed = "due_date_changed"
    job_set = "job_set"


# Keys that would carry the source entry's text. Titles are allowed here on
# purpose (D3); the entry body never is.
FORBIDDEN_TASK_AUDIT_KEYS: frozenset[str] = frozenset(
    {
        "body_text",
        "body",
        "raw",
        "raw_text",
        "transcript",
        "payload",
        "bytes",
        "content",
        "snapshot",
    }
)


def validate_task_audit_detail(changed_fields: dict) -> dict:
    """Reject keys that would put the entry's body into the task audit.

    Raises ``ValueError`` on a forbidden key at any nesting depth.
    """

    def _walk(obj: object, path: str) -> None:
        if isinstance(obj, dict):
            for key, value in obj.items():
                if str(key).lower() in FORBIDDEN_TASK_AUDIT_KEYS:
                    raise ValueError(
                        f"task audit must not carry entry content: "
                        f"forbidden key {key!r} at {path or '<root>'}"
                    )
                _walk(value, f"{path}.{key}" if path else str(key))
        elif isinstance(obj, list):
            for i, value in enumerate(obj):
                _walk(value, f"{path}[{i}]")

    _walk(changed_fields, "")
    return changed_fields


class Task(Base, TimestampMixin):
    """One follow-up item made from a Site Log entry."""

    __tablename__ = "tasks"

    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, server_default=_TENANT_DEFAULT
    )

    # The task's own job: copied from the entry at creation (it may be
    # NULL), then changed only by an explicit, audited action. NO ACTION,
    # like every Site Log FK: a referenced Job cannot be hard-deleted.
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.job_id"), nullable=True
    )

    # Evidence ref: the entry and the exact revision the task was made from.
    source_site_log_event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("site_log_events.site_log_event_id"),
        nullable=False,
    )
    source_revision_no: Mapped[int] = mapped_column(Integer, nullable=False)

    title: Mapped[str] = mapped_column(String(TITLE_MAX_CHARS), nullable=False)

    status: Mapped[TaskStatus] = mapped_column(
        SqlaEnum(
            TaskStatus,
            name="task_status",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=TaskStatus.to_do,
        server_default=TaskStatus.to_do.value,
    )

    # At most one assignee, by construction (a single column).
    assignee_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=True
    )

    # A calendar date only: no time, no timezone.
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    origin: Mapped[TaskOrigin] = mapped_column(
        SqlaEnum(
            TaskOrigin,
            name="task_origin",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=TaskOrigin.manual,
        server_default=TaskOrigin.manual.value,
    )

    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=False
    )

    # Device-generated idempotency key: a replay of the same create returns
    # the existing task instead of making a second one.
    create_client_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "created_by_user_id",
            "create_client_id",
            name="uq_task_create_client",
        ),
        CheckConstraint(
            "char_length(btrim(title)) >= 1", name="ck_task_title_not_blank"
        ),
        CheckConstraint(
            "source_revision_no >= 1", name="ck_task_source_revision_no_ge_1"
        ),
        Index("ix_task_job_status", "job_id", "status"),
        Index("ix_task_assignee_status", "assignee_user_id", "status"),
        Index("ix_task_source_event", "source_site_log_event_id"),
        Index("ix_task_creator_created", "created_by_user_id", "created_at"),
    )


class TaskAuditLog(Base):
    """Append-only task history. Never updated, never deleted."""

    __tablename__ = "task_audit_log"

    audit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, server_default=_TENANT_DEFAULT
    )

    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.task_id"), nullable=False
    )

    actor_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=False
    )

    # Application-validated against TaskAuditAction.
    action: Mapped[str] = mapped_column(String(32), nullable=False)

    changed_fields: Mapped[dict] = mapped_column(JSONB, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("clock_timestamp()"),
        nullable=False,
    )

    __table_args__ = (
        Index("ix_task_audit_task_created", "task_id", "created_at"),
    )
