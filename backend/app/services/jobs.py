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

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.text import normalize_alias
from app.models.category import Category
from app.models.job import Job, JobAlias, JobCategoryBudget
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


async def update_job(
    db: AsyncSession,
    job_id: uuid.UUID,
    *,
    job_name: str | None = None,
    job_code: str | None = None,
    site_address: str | None = None,
    contract_value_ex_gst: Decimal | None = None,
    total_budget_ex_gst: Decimal | None = None,
    target_profit_ratio_pct: Decimal | None = None,
    warning_amber_pct: Decimal | None = None,
    warning_red_pct: Decimal | None = None,
    status=None,
) -> Job:
    """Partial update; only non-``None`` fields are applied.

    This matches Task 6's :func:`update_category` convention: the caller
    passes exactly the fields it wants to change; everything else is
    left untouched. Raises :class:`JobNotFound` on a missing id.

    Phase 3 Lite+ adds three optional percent fields. Note the
    convention limitation: passing ``None`` here does NOT clear an
    existing stored value — it simply skips that field. Callers that
    need to clear a stored override (e.g. revert ``warning_amber_pct``
    to NULL so the system default re-applies) need to use a sentinel
    or a dedicated DELETE endpoint; that flow is out of scope for
    Phase 3 Lite+ and is left as future work.
    """
    job = await get_job(db, job_id)
    updates = {
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
    for k, v in updates.items():
        if v is not None:
            setattr(job, k, v)
    await db.flush()
    await db.refresh(job, ["aliases", "category_budgets"])
    return job


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
