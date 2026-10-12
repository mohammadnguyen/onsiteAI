"""Task service (Site Log → Task, plan v2).

Sole writer of ``tasks`` and ``task_audit_log``. Reads Site Log entries and
their revisions; never writes to any Site Log table, so a task can never
change the record it came from.

Modules, dependencies one way: ``errors`` → ``access`` → ``rules`` →
``views`` → ``create`` / ``update``.

* :mod:`errors` — the exception types; the API maps them to status codes.
* :mod:`access` — who may see and change a task. Pure.
* :mod:`rules` — proposed-title derivation, title cleaning, the create
  fingerprint, audit rows, assignee and job checks.
* :mod:`views` — access resolution, the view model, the reads.
* :mod:`create` — create from an entry, idempotent per creator and key.
* :mod:`update` — PATCH, one audit row per changed field.

Callers use ``from app.services import tasks as task_svc``.
"""

from __future__ import annotations

from .create import CreateResult, create_task
from .errors import (
    TaskError,
    TaskFingerprintMismatch,
    TaskForbidden,
    TaskJobCompleted,
    TaskJobNotFound,
    TaskNotFound,
    TaskReasonRequired,
    TaskSourceNotFound,
    TaskSourceWithdrawn,
    TaskValidationError,
)
from .rules import proposed_title_from
from .update import UPDATABLE_FIELDS, update_task
from .views import (
    TASK_PAGE_DEFAULT,
    TASK_PAGE_MAX,
    TaskPermissions,
    TaskView,
    get_task,
    list_event_tasks,
    list_tasks,
)

__all__ = [
    "TASK_PAGE_DEFAULT",
    "TASK_PAGE_MAX",
    "UPDATABLE_FIELDS",
    "CreateResult",
    "TaskError",
    "TaskFingerprintMismatch",
    "TaskForbidden",
    "TaskJobCompleted",
    "TaskJobNotFound",
    "TaskNotFound",
    "TaskPermissions",
    "TaskReasonRequired",
    "TaskSourceNotFound",
    "TaskSourceWithdrawn",
    "TaskValidationError",
    "TaskView",
    "create_task",
    "get_task",
    "list_event_tasks",
    "list_tasks",
    "proposed_title_from",
    "update_task",
]
