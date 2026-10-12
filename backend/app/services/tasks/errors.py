"""Domain exceptions of the task service. The API layer maps them to
status codes; services never raise HTTP errors themselves."""

from __future__ import annotations


class TaskError(Exception):
    """Base class. ``message`` is safe to return to the caller."""

    message = "Task error"

    def __init__(self, message: str | None = None) -> None:
        if message is not None:
            self.message = message
        super().__init__(self.message)


class TaskNotFound(TaskError):
    """Unknown task, or one the caller may not view (existence hidden)."""

    message = "Task not found"


class TaskSourceNotFound(TaskError):
    """Unknown Site Log entry, or one the caller may not read."""

    message = "Site log event not found"


class TaskSourceWithdrawn(TaskError):
    message = "The source entry is withdrawn"


class TaskFingerprintMismatch(TaskError):
    message = "create_client_id reused with a different task"


class TaskForbidden(TaskError):
    """The caller can view the task but may not make this change."""

    message = "Not allowed to change this task"


class TaskValidationError(TaskError):
    message = "Invalid task"


class TaskJobNotFound(TaskError):
    message = "Job not found"


class TaskJobCompleted(TaskError):
    message = "Job is completed"


class TaskReasonRequired(TaskError):
    message = "A non-empty reason is required"
