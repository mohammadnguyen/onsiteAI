"""Job-related business logic. HTTP-agnostic; raises domain exceptions.

Each function takes an :class:`AsyncSession` plus typed inputs and either
returns a persisted model or raises one of the domain exceptions defined
at the top of this module. The HTTP layer (``app/api/jobs.py``) is the
only caller and is responsible for mapping these exceptions onto the
correct status codes.

Duplicate checks (aliases, budgets) are performed as a pre-SELECT inside
the same transaction rather than relying on the DB's UNIQUE constraint
to raise. This follows the same SAVEPOINT-hygiene rationale as Task 6:
under pytest's rollback-on-teardown transaction a failed INSERT would
otherwise poison the enclosing SAVEPOINT. The UNIQUE constraint remains
the real backstop for the race window.
"""

from __future__ import annotations

import enum
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.text import normalize_alias
from app.models.category import Category
from app.models.expense import Expense
from app.models.job import Job, JobAlias, JobCategoryBudget, JobStatus
from app.models.job_audit_log import JobAuditLog
from app.models.review_queue import ExpenseReviewQueue
from app.models.user import LanguageCode, User


class JobNotFound(Exception):
    """Raised when a job_id doesn't resolve to a persisted row."""

    def __init__(self, job_id: uuid.UUID):
        self.job_id = job_id
        super().__init__(f"Job {job_id} not found")


class CategoryNotFound(Exception):
    """Raised when adding a budget with an unknown ``category_id``."""

    def __init__(self, category_id: uuid.UUID):
        self.category_id = category_id
        super().__init__(f"Category {category_id} not found")


class DuplicateAlias(Exception):
    """Raised when an alias's normalised form already exists (any job)."""

    def __init__(self, normalized: str):
        self.normalized = normalized
        super().__init__(f"Alias {normalized!r} already exists")


class DuplicateBudget(Exception):
    """Raised when a (job_id, category_id) budget row already exists."""

    def __init__(self, job_id: uuid.UUID, category_id: uuid.UUID):
        self.job_id = job_id
        self.category_id = category_id
        super().__init__(
            f"Budget for job {job_id} + category {category_id} already exists"
        )


class DuplicateJobCode(Exception):
    """Raised when PATCH /jobs/{id} would set ``job_code`` to a value
    that is already in use by a different job.

    Job Lifecycle v1A-1: pre-checked in :func:`update_job` so we never
    rely on the DB's UNIQUE constraint to raise an ``IntegrityError``
    inside the test fixture's outer transaction (which would poison
    the session for any subsequent request in the same test).
    """

    def __init__(self, job_code: str):
        self.job_code = job_code
        super().__init__(f"Job code {job_code!r} already exists")


class JobHasDependencies(Exception):
    """Raised when :func:`delete_empty_job` is called on a job that
    has dependencies which would make hard delete unsafe.

    Job Lifecycle v1A-3: only truly empty jobs (zero expenses + zero
    review-queue rows) may be deleted. Jobs with any dependency must
    be archived (PATCH status=completed) instead. The HTTP layer maps
    this to a 409 with the carried ``detail`` string verbatim.

    ``detail`` is a user-facing message that the admin web renders
    inside the confirm dialog (e.g.
    "Job has 3 expenses and cannot be deleted. Archive it instead.")
    """

    def __init__(self, detail: str):
        self.detail = detail
        super().__init__(detail)


async def create_job(
    db: AsyncSession,
    *,
    created_by: User,
    job_name: str,
    job_code: str | None = None,
    site_address: str | None = None,
    contract_value_ex_gst: Decimal | None = None,
    total_budget_ex_gst: Decimal | None = None,
    target_profit_ratio_pct: Decimal | None = None,
    warning_amber_pct: Decimal | None = None,
    warning_red_pct: Decimal | None = None,
    status=None,
) -> Job:
    """Insert a new :class:`Job` owned by ``created_by``.

    Phase 3 Lite+ adds three optional percent fields. Pydantic on
    :class:`~app.schemas.job.JobCreate` enforces the value ranges and
    the amber-lt-red cross-field rule before this is reached. The DB
    CHECK constraints (added in migration ``b3e7a8f1c042``) are the
    backstop.
    """
    kwargs: dict = {
        "job_id": uuid.uuid4(),
        "job_name": job_name,
        "job_code": job_code,
        "site_address": site_address,
        "contract_value_ex_gst": contract_value_ex_gst,
        "total_budget_ex_gst": total_budget_ex_gst,
        "target_profit_ratio_pct": target_profit_ratio_pct,
        "warning_amber_pct": warning_amber_pct,
        "warning_red_pct": warning_red_pct,
        "created_by": created_by.user_id,
    }
    if status is not None:
        kwargs["status"] = status
    job = Job(**kwargs)
    db.add(job)
    await db.flush()
    # Eager-load the relationships so callers can serialise the compact
    # JobPublic or the detail view without triggering lazy I/O outside
    # the request's session scope.
    await db.refresh(job, ["aliases", "category_budgets"])
    return job


async def list_jobs(db: AsyncSession) -> list[Job]:
    """Return all jobs, newest first, with relationships eager-loaded."""
    q = (
        select(Job)
        .options(
            selectinload(Job.aliases),
            selectinload(Job.category_budgets),
        )
        .order_by(Job.created_at.desc())
    )
    return list((await db.execute(q)).scalars().all())


async def get_job(db: AsyncSession, job_id: uuid.UUID) -> Job:
    """Fetch one job by id with aliases + category budgets eager-loaded.

    Raises :class:`JobNotFound` if the id doesn't match.
    """
    q = (
        select(Job)
        .where(Job.job_id == job_id)
        .options(
            selectinload(Job.aliases),
            selectinload(Job.category_budgets),
        )
    )
    job = (await db.execute(q)).scalar_one_or_none()
    if job is None:
        raise JobNotFound(job_id)
    return job


# Sentinel for "argument not provided" so the service can distinguish
# "caller did not mention this field" from "caller explicitly sent null".
# Phase 3 Lite+ correction: the API exposes JSON-null as a clear, which
# the route layer (``app/api/jobs.py``) translates by extracting only the
# fields the user actually included via ``model_dump(exclude_unset=True)``
# and forwarding them as kwargs. Anything not sent stays at the sentinel
# default, so this loop leaves the column alone.
_UNSET: object = object()


# ---------------------------------------------------------------------------
# Job Lifecycle v1A-1 — Edit + Audit Foundation
# ---------------------------------------------------------------------------

# Columns whose changes are recorded in ``job_audit_log``. Status is
# included even though v1A-1 ships no archive/reopen UI; this means
# any PATCH that flips status (curl, future v1A-2 UI, automation)
# automatically produces an audit row with the right ``action``
# without further code changes.
_AUDITABLE_JOB_FIELDS: tuple[str, ...] = (
    "job_name",
    "job_code",
    "site_address",
    "status",
)


def _coerce_job_audit_value(value: Any) -> Any:
    """Convert a value into a JSON-serialisable form for the JSONB diff.

    Intentionally NOT imported from :mod:`app.services.expenses` —
    that module's ``_coerce_audit_value`` is a private helper and the
    code-quality contract for v1A-1 says we keep job-side coercion
    local to avoid a cross-module dependency on a private symbol.
    The duplication is small (a few isinstance checks) and limited to
    the value types the auditable job fields can take (``str``,
    :class:`~app.models.job.JobStatus`, ``None``).
    """
    if value is None:
        return None
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


def _derive_audit_action(changed_fields: dict[str, dict[str, Any]]) -> str:
    """Map a non-empty ``changed_fields`` dict to the canonical action.

    Priority: status transitions outrank plain field edits because
    archive/reopen are lifecycle events. Order of precedence:

    * status → ``JobStatus.completed`` → ``"archive"``
    * status → ``JobStatus.active``    → ``"reopen"``
    * any other field changed          → ``"edit"``

    Callers are expected to call this only when ``changed_fields`` is
    non-empty; the no-op short-circuit in :func:`update_job` skips the
    audit-row write entirely when nothing changed.
    """
    if "status" in changed_fields:
        new_value = changed_fields["status"]["new"]
        if new_value == JobStatus.completed.value:
            return "archive"
        if new_value == JobStatus.active.value:
            return "reopen"
    return "edit"


async def update_job(
    db: AsyncSession,
    job_id: uuid.UUID,
    *,
    actor: User | None = None,
    job_name: str | object = _UNSET,
    job_code: str | None | object = _UNSET,
    site_address: str | None | object = _UNSET,
    contract_value_ex_gst: Decimal | None | object = _UNSET,
    total_budget_ex_gst: Decimal | None | object = _UNSET,
    target_profit_ratio_pct: Decimal | None | object = _UNSET,
    warning_amber_pct: Decimal | None | object = _UNSET,
    warning_red_pct: Decimal | None | object = _UNSET,
    status: object = _UNSET,
) -> Job:
    """Partial update of a :class:`Job`. Two distinct caller intents:

    * **Field omitted (kwarg not passed)** → leave the column alone.
    * **Field set to ``None``** → clear the column to NULL. Only valid
      for nullable columns; the DB CHECK / NOT NULL constraints catch
      misuse.

    Raises :class:`JobNotFound` on a missing id. Cross-field constraint
    violations (e.g. patching ``warning_amber_pct`` to a value that's
    no longer strictly less than the stored ``warning_red_pct``) are
    caught by the DB ``ck_jobs_warning_amber_lt_red`` CHECK and surface
    as ``IntegrityError`` — the API layer translates that to a 422.

    Phase 3 Lite+ correction (commit pending): the prior behaviour was
    "any None means skip", which made it impossible for the Job
    Settings form to clear ``target_profit_ratio_pct`` /
    ``warning_amber_pct`` / ``warning_red_pct`` /
    ``contract_value_ex_gst`` / ``total_budget_ex_gst`` back to NULL
    once they had been set — the only escape hatch was direct SQL.
    The new semantics align with how the Pydantic ``JobUpdate`` body
    is typed (``T | None``) and how the front-end form already
    submitted ``null`` for cleared inputs.

    Job Lifecycle v1A-1 — audit foundation
    --------------------------------------
    When ``actor`` is supplied AND at least one of the
    :data:`_AUDITABLE_JOB_FIELDS` (``job_name``, ``job_code``,
    ``site_address``, ``status``) actually changes value, a single
    :class:`~app.models.job_audit_log.JobAuditLog` row is written in
    the same transaction recording the pre/post diff. No-op PATCHes
    (everything unchanged, or only non-auditable fields like budgets
    changed) produce no audit row. ``actor=None`` skips the audit
    write entirely — a deliberate escape hatch for internal callers
    (tests, scripts, future automation) that legitimately do not have
    a user context.
    """
    job = await get_job(db, job_id)

    # Snapshot the pre-edit values of every auditable field BEFORE we
    # apply the patch, so we can compute the diff after.
    pre_audit: dict[str, Any] = {
        f: getattr(job, f) for f in _AUDITABLE_JOB_FIELDS
    }
    # Pre-edit snapshots for the audit row's denormalized identifier
    # columns (kept stable across renames + post-delete queries).
    pre_name_snapshot = job.job_name
    pre_code_snapshot = job.job_code

    # Job Lifecycle v1A-1: pre-check uniqueness of ``job_code`` before
    # the row write so we can raise a clean :class:`DuplicateJobCode`
    # exception instead of letting the DB UNIQUE constraint surface as
    # an ``IntegrityError`` mid-flush (which would leave the test
    # fixture's session in pending-rollback state and block any
    # subsequent request in the same test). Same pre-INSERT-check
    # pattern as :func:`add_alias`.
    if job_code is not _UNSET and job_code is not None:
        new_code = str(job_code)
        if new_code != job.job_code:
            existing = (
                await db.execute(
                    select(Job).where(
                        Job.job_code == new_code,
                        Job.job_id != job_id,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                raise DuplicateJobCode(new_code)

    candidates = {
        "job_name": job_name,
        "job_code": job_code,
        "site_address": site_address,
        "contract_value_ex_gst": contract_value_ex_gst,
        "total_budget_ex_gst": total_budget_ex_gst,
        "target_profit_ratio_pct": target_profit_ratio_pct,
        "warning_amber_pct": warning_amber_pct,
        "warning_red_pct": warning_red_pct,
        "status": status,
    }
    for k, v in candidates.items():
        if v is _UNSET:
            continue
        setattr(job, k, v)
    await db.flush()

    # Compute the diff. Only fields that actually changed value land
    # in changed_fields; this implements the no-op short-circuit (no
    # row written when nothing changed) per v1A-1 spec.
    if actor is not None:
        changed_fields: dict[str, dict[str, Any]] = {}
        for f in _AUDITABLE_JOB_FIELDS:
            old = pre_audit[f]
            new = getattr(job, f)
            if old != new:
                changed_fields[f] = {
                    "old": _coerce_job_audit_value(old),
                    "new": _coerce_job_audit_value(new),
                }
        if changed_fields:
            audit_row = JobAuditLog(
                audit_id=uuid.uuid4(),
                job_id=job.job_id,
                job_name_snapshot=pre_name_snapshot,
                job_code_snapshot=pre_code_snapshot,
                actor_user_id=actor.user_id,
                action=_derive_audit_action(changed_fields),
                changed_fields=changed_fields,
            )
            db.add(audit_row)
            await db.flush()

    await db.refresh(job, ["aliases", "category_budgets"])
    return job


async def list_job_audit(
    db: AsyncSession,
    job_id: uuid.UUID,
) -> list[JobAuditLog]:
    """Return audit-log rows for a job, newest first.

    Raises :class:`JobNotFound` if the live job_id does not resolve.
    Does NOT yet surface audit rows for hard-deleted jobs by their
    historical id — that pathway lands in v1A-3 when the
    ``snapshot``-based lookup is wired up.
    """
    _ = await get_job(db, job_id)
    q = (
        select(JobAuditLog)
        .where(JobAuditLog.job_id == job_id)
        .order_by(JobAuditLog.created_at.desc())
    )
    return list((await db.execute(q)).scalars().all())


async def add_alias(
    db: AsyncSession,
    job_id: uuid.UUID,
    *,
    alias_text: str,
    language_code: LanguageCode | None = None,
) -> JobAlias:
    """Create a :class:`JobAlias` under ``job_id``.

    Raises :class:`JobNotFound` if the parent job doesn't exist and
    :class:`DuplicateAlias` if the normalised form is already claimed
    (globally, not per-job — see model docstring).
    """
    # 404 before 409 so callers get the more specific error first.
    _ = await get_job(db, job_id)

    normalized = normalize_alias(alias_text)
    existing = (
        await db.execute(
            select(JobAlias).where(JobAlias.alias_text_normalized == normalized)
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise DuplicateAlias(normalized)

    alias = JobAlias(
        alias_id=uuid.uuid4(),
        job_id=job_id,
        alias_text=alias_text,
        language_code=language_code,
    )
    db.add(alias)
    await db.flush()
    return alias


async def add_category_budget(
    db: AsyncSession,
    job_id: uuid.UUID,
    *,
    category_id: uuid.UUID,
    budget_amount_ex_gst: Decimal,
) -> JobCategoryBudget:
    """Create a :class:`JobCategoryBudget` row.

    Raises :class:`JobNotFound` if the parent job is missing,
    :class:`CategoryNotFound` if the category is missing, and
    :class:`DuplicateBudget` if ``(job_id, category_id)`` already exists.
    """
    _ = await get_job(db, job_id)  # 404 if missing

    cat = (
        await db.execute(
            select(Category).where(Category.category_id == category_id)
        )
    ).scalar_one_or_none()
    if cat is None:
        raise CategoryNotFound(category_id)

    existing = (
        await db.execute(
            select(JobCategoryBudget).where(
                JobCategoryBudget.job_id == job_id,
                JobCategoryBudget.category_id == category_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise DuplicateBudget(job_id, category_id)

    budget = JobCategoryBudget(
        budget_id=uuid.uuid4(),
        job_id=job_id,
        category_id=category_id,
        budget_amount_ex_gst=budget_amount_ex_gst,
    )
    db.add(budget)
    await db.flush()
    # Eager-load the joined category so the HTTP response body can inline
    # ``CategoryPublic`` without an extra query outside the session scope.
    await db.refresh(budget, ["category"])
    return budget


# ---------------------------------------------------------------------------
# Job Lifecycle v1A-3 — Delete Empty Job
# ---------------------------------------------------------------------------


async def delete_empty_job(
    db: AsyncSession,
    *,
    admin: User,
    job_id: uuid.UUID,
) -> None:
    """Hard-delete a job that has no expenses and no review-queue rows.

    Raises :class:`JobNotFound` if the id does not resolve, or
    :class:`JobHasDependencies` if the job has any dependency that
    makes hard delete unsafe. The HTTP layer maps these to 404 and
    409 respectively.

    Dependency checks (both run; the queue check is defence-in-depth
    because ``expense_review_queue`` rows already CASCADE-delete from
    ``expenses``, so if the expense count is zero the queue count is
    necessarily zero too — but explicitly checking both means a
    future schema change that breaks the cascade is caught here):

    * ``SELECT COUNT(*) FROM expenses WHERE job_id = $1`` must be 0.
    * ``SELECT COUNT(*) FROM expense_review_queue erq
        JOIN expenses e ON erq.expense_id = e.expense_id
        WHERE e.job_id = $1`` must be 0.

    Audit row is written BEFORE the SQL DELETE so the trail survives
    via ``ON DELETE SET NULL`` on ``job_audit_log.job_id`` (v1A-1
    design). Snapshot columns (``job_name_snapshot``,
    ``job_code_snapshot``) preserve the human-meaningful identifier
    after the parent row is gone. ``action="delete"`` for quick
    filtering.

    Aliases (``ondelete="CASCADE"`` on ``JobAlias.job_id``) and
    per-category budgets (``ondelete="CASCADE"`` on
    ``JobCategoryBudget.job_id``) cascade-delete with the parent
    via existing model FK config (no application-side cleanup).

    No ``reason`` parameter: v1A-3 chose R1=Option B (no reason
    input anywhere) because the audit table has no ``reason``
    column. When a ``reason`` column lands in a future schema
    change, both this signature and the HTTP endpoint can be
    extended in the same batch.
    """
    job = await get_job(db, job_id)  # raises JobNotFound

    # Dependency check 1: expenses.
    expense_count = int(
        (
            await db.execute(
                select(func.count()).where(Expense.job_id == job_id)
            )
        ).scalar()
        or 0
    )
    if expense_count > 0:
        noun = "expense" if expense_count == 1 else "expenses"
        raise JobHasDependencies(
            f"Job has {expense_count} {noun} and cannot be deleted. "
            "Archive it instead."
        )

    # Dependency check 2: review-queue rows (defence-in-depth).
    queue_count = int(
        (
            await db.execute(
                select(func.count())
                .select_from(ExpenseReviewQueue)
                .join(
                    Expense,
                    ExpenseReviewQueue.expense_id == Expense.expense_id,
                )
                .where(Expense.job_id == job_id)
            )
        ).scalar()
        or 0
    )
    if queue_count > 0:
        noun = "row" if queue_count == 1 else "rows"
        raise JobHasDependencies(
            f"Job has {queue_count} review queue {noun} and cannot be "
            "deleted. Archive it instead."
        )

    # Pre-delete audit row. Snapshots reflect the pre-delete state of
    # the job (the only state available before the DELETE). action=
    # "delete" lets the audit-trail UI render a dedicated label
    # without parsing changed_fields.
    audit = JobAuditLog(
        audit_id=uuid.uuid4(),
        job_id=job.job_id,
        job_name_snapshot=job.job_name,
        job_code_snapshot=job.job_code,
        actor_user_id=admin.user_id,
        action="delete",
        changed_fields={
            "_lifecycle": {
                "old": _coerce_job_audit_value(job.status),
                "new": "deleted",
            }
        },
    )
    db.add(audit)
    await db.flush()

    # The actual delete. Aliases + category budgets cascade via
    # existing ondelete="CASCADE". Audit row's job_id is set to NULL
    # by its own ondelete="SET NULL" FK so the row remains queryable.
    await db.delete(job)
    await db.flush()
