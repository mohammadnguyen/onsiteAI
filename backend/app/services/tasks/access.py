"""Who may see and change a task (plan v2 §3b). Pure helpers: no IO, no
writes. Built only on mechanisms that already exist — role, ``is_active``
and the Site Log read rules — with no job membership and no new user
directory.

* View: admin; the creator; the assignee; or, when the task has a job, any
  user who may read that job (``can_read_job`` — today, any active user;
  the same rule as Site Log entries that have a job).
* Change status: admin, creator, assignee.
* Edit title or due date: admin, creator.
* Change the assignee: admin; the creator only while the task is unassigned
  or assigned to the creator - a task assigned to someone else is the
  admin's to change (founder ruling 2026-10-13, supplementing D1).
* Assign: an admin may pick any active user; anyone else only themselves or
  nobody (D1).
* Job: set it when it is empty — admin or creator; change it once set —
  admin only, with a reason.

Assigning someone a task never grants them the source entry or its files:
those stay behind ``can_read_event``.
"""

from __future__ import annotations

from sqlalchemy import or_
from sqlalchemy.sql.elements import ColumnElement

from app.models.job import Job
from app.models.task import Task
from app.models.user import User, UserRole
from app.services.site_log_access import can_read_job


def _is_admin(user: User) -> bool:
    return user.role == UserRole.admin


def _is_creator(user: User, task: Task) -> bool:
    return task.created_by_user_id == user.user_id


def _is_assignee(user: User, task: Task) -> bool:
    return task.assignee_user_id is not None and task.assignee_user_id == user.user_id


def can_view_task(user: User, task: Task, job: Job | None) -> bool:
    """``job`` must be the task's own job (or None)."""
    if _is_admin(user) or _is_creator(user, task) or _is_assignee(user, task):
        return True
    if task.job_id is None:
        return False
    return can_read_job(user, job)


def visible_tasks_clause(user: User) -> ColumnElement[bool] | None:
    """SQL form of :func:`can_view_task` for list queries; None = no filter.

    It restates ``can_read_job`` as "the task has a job", which is exact
    while that rule is "any active user" (the caller of a list is always an
    active user). A test compares this clause with :func:`can_view_task`
    over a matrix of tasks, so the two cannot drift apart silently.
    """
    if _is_admin(user):
        return None
    return or_(
        Task.created_by_user_id == user.user_id,
        Task.assignee_user_id == user.user_id,
        Task.job_id.is_not(None),
    )


def can_change_status(user: User, task: Task) -> bool:
    return _is_admin(user) or _is_creator(user, task) or _is_assignee(user, task)


def can_edit_task(user: User, task: Task) -> bool:
    """Title and due date."""
    return _is_admin(user) or _is_creator(user, task)


def can_change_assignee(user: User, task: Task) -> bool:
    """Who the task is assigned to (D1 supplement, 2026-10-13): an admin
    always; the creator only while it is unassigned or assigned to them, so
    a creator can neither clear nor take over someone else's assignment."""
    if _is_admin(user):
        return True
    return _is_creator(user, task) and task.assignee_user_id in (None, user.user_id)


def can_assign_others(user: User) -> bool:
    return _is_admin(user)


def can_set_job(user: User, task: Task) -> bool:
    """First set, from no job."""
    return task.job_id is None and (_is_admin(user) or _is_creator(user, task))


def can_change_job(user: User, task: Task) -> bool:
    """Move a task that already has a job: admin only (a reason is required)."""
    return task.job_id is not None and _is_admin(user)
