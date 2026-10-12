"""tasks: follow-up items made from Site Log entries (Site Log → Task, plan v2)

Two new tables, purely additive. No existing table, column or type is
modified, so the previous backend image runs unchanged against this schema.

* ``tasks`` — one follow-up item created by an explicit human action from a
  Site Log entry. ``job_id`` is the task's own job (copied from the entry at
  creation, never moved by a later entry assign or re-link).
  ``source_site_log_event_id`` + ``source_revision_no`` are the evidence
  reference. ``create_client_id`` is the device idempotency key, unique per
  tenant and creator.
* ``task_audit_log`` — append-only history; the ``created`` row is the full
  record of the human confirmation (proposed title, edit flag, final values,
  actor, time).

Two new enum types: ``task_status`` and ``task_origin``.

Rollback (plan v2 §9):

* The NORMAL rollback is the previous backend image with these tables and
  their data KEPT. The old backend ignores them, and its startup schema-head
  check only logs a mismatch. While rolled back there is no Task feature.
* ``downgrade()`` below drops both tables and both types: it DELETES EVERY
  TASK and its history. It is not a rollback step. Running it is a separate
  destructive operation that needs its own approval and a backup first.

Revision ID: e1f2a3b4c5d6
Revises: d9e0f1a2b3c4
Create Date: 2026-10-13
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

# revision identifiers, used by Alembic.
revision: str = "e1f2a3b4c5d6"
down_revision: Union[str, Sequence[str], None] = "d9e0f1a2b3c4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TENANT_DEFAULT = sa.text("'00000000-0000-0000-0000-000000000001'::uuid")

task_status_enum = sa.Enum(
    "to_do", "in_progress", "blocked", "done", name="task_status"
)
task_origin_enum = sa.Enum("manual", name="task_origin")


def upgrade() -> None:
    op.create_table(
        "tasks",
        sa.Column("task_id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            nullable=False,
            server_default=TENANT_DEFAULT,
        ),
        sa.Column(
            "job_id",
            UUID(as_uuid=True),
            sa.ForeignKey("jobs.job_id"),
            nullable=True,
        ),
        sa.Column(
            "source_site_log_event_id",
            UUID(as_uuid=True),
            sa.ForeignKey("site_log_events.site_log_event_id"),
            nullable=False,
        ),
        sa.Column("source_revision_no", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column(
            "status",
            task_status_enum,
            nullable=False,
            server_default="to_do",
        ),
        sa.Column(
            "assignee_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.user_id"),
            nullable=True,
        ),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column(
            "origin",
            task_origin_enum,
            nullable=False,
            server_default="manual",
        ),
        sa.Column(
            "created_by_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.user_id"),
            nullable=False,
        ),
        sa.Column("create_client_id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "created_by_user_id",
            "create_client_id",
            name="uq_task_create_client",
        ),
        sa.CheckConstraint(
            "char_length(btrim(title)) >= 1", name="ck_task_title_not_blank"
        ),
        sa.CheckConstraint(
            "source_revision_no >= 1", name="ck_task_source_revision_no_ge_1"
        ),
    )
    op.create_index("ix_task_job_status", "tasks", ["job_id", "status"])
    op.create_index(
        "ix_task_assignee_status", "tasks", ["assignee_user_id", "status"]
    )
    op.create_index(
        "ix_task_source_event", "tasks", ["source_site_log_event_id"]
    )
    op.create_index(
        "ix_task_creator_created", "tasks", ["created_by_user_id", "created_at"]
    )

    op.create_table(
        "task_audit_log",
        sa.Column("audit_id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            nullable=False,
            server_default=TENANT_DEFAULT,
        ),
        sa.Column(
            "task_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tasks.task_id"),
            nullable=False,
        ),
        sa.Column(
            "actor_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.user_id"),
            nullable=False,
        ),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("changed_fields", JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_task_audit_task_created", "task_audit_log", ["task_id", "created_at"]
    )


def downgrade() -> None:
    # DESTRUCTIVE: deletes every task and its history. Not a rollback step —
    # see the module docstring.
    op.drop_index("ix_task_audit_task_created", table_name="task_audit_log")
    op.drop_table("task_audit_log")
    op.drop_index("ix_task_creator_created", table_name="tasks")
    op.drop_index("ix_task_source_event", table_name="tasks")
    op.drop_index("ix_task_assignee_status", table_name="tasks")
    op.drop_index("ix_task_job_status", table_name="tasks")
    op.drop_table("tasks")
    task_origin_enum.drop(op.get_bind(), checkfirst=True)
    task_status_enum.drop(op.get_bind(), checkfirst=True)
