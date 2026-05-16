"""Job / alias / category-budget HTTP routes.

Thin layer that forwards to :mod:`app.services.jobs` and maps the
service's domain exceptions onto HTTP status codes:

* :class:`JobNotFound` / :class:`CategoryNotFound` -> 404
* :class:`DuplicateAlias` / :class:`DuplicateBudget` -> 409

Auth policy:

* ``POST`` / ``PATCH`` of jobs, aliases, and budgets are all admin-only
  (``Depends(require_admin)``).
* ``GET /jobs`` and ``GET /jobs/{id}`` are accessible to any
  authenticated caller — admin and contributor both see the same list
  in Phase 1; assignment-scoped filtering is a later-phase concern.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_user, require_admin
from app.models.job import Job
from app.models.user import User
from app.schemas.budget_summary import JobBudgetSummary
from app.schemas.job import (
    JobAliasCreate,
    JobAliasPublic,
    JobAuditRow,
    JobCategoryBudgetCreate,
    JobCategoryBudgetPublic,
    JobCreate,
    JobPublic,
    JobUpdate,
    JobWithDetailPublic,
)
from app.services.budget_summary import summarize_job, summarize_jobs
from app.services.jobs import (
    CategoryNotFound,
    DuplicateAlias,
    DuplicateBudget,
    DuplicateJobCode,
    JobNotFound,
    add_alias,
    add_category_budget,
    create_job,
    get_job,
    list_job_audit,
    list_jobs,
    update_job,
)

router = APIRouter(tags=["jobs"])


@router.post(
    "",
    response_model=JobPublic,
    status_code=status.HTTP_201_CREATED,
)
async def create_job_endpoint(
    body: JobCreate,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> Job:
    """Create a job (admin only).

    Mobile Job Management Lite hardening: a duplicate ``job_code`` (the
    only UNIQUE constraint reachable on this endpoint today) used to
    surface as SQLAlchemy's default 500. We now translate it to a 409
    with a friendly detail so the mobile UI can render an actionable
    error. Other ``IntegrityError`` causes (e.g. cross-field CHECK
    constraint violations that Pydantic doesn't catch) fall through to
    a 422 mirroring the PATCH route's pattern, so we never return 500
    on a constraint violation.
    """
    try:
        return await create_job(
            db,
            created_by=admin,
            job_name=body.job_name,
            job_code=body.job_code,
            site_address=body.site_address,
            contract_value_ex_gst=body.contract_value_ex_gst,
            total_budget_ex_gst=body.total_budget_ex_gst,
            target_profit_ratio_pct=body.target_profit_ratio_pct,
            warning_amber_pct=body.warning_amber_pct,
            warning_red_pct=body.warning_red_pct,
            status=body.status,
        )
    except IntegrityError as exc:
        err_text = str(exc.orig).lower()
        if "unique" in err_text or "duplicate key" in err_text:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Job code already exists",
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Database constraint violated: {exc.orig}",
        ) from exc


@router.get(
    "",
    response_model=list[JobPublic],
    status_code=status.HTTP_200_OK,
)
async def list_jobs_endpoint(
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[JobPublic]:
    """List all jobs (any authenticated caller).

    Phase 3 Lite: each row carries a ``summary`` field with the per-job
    ex-GST aggregates used by the dashboard. The summary is populated
    for every row — jobs with no expenses get an all-zero summary so
    the UI never has to special-case missing data. The auth posture is
    unchanged from Phase 1 (any authenticated user can list jobs); the
    dashboard surface that consumes ``summary`` is admin-only by route
    composition (admin nav), not by route auth.
    """
    jobs = await list_jobs(db)
    summaries = await summarize_jobs(db, job_ids=[j.job_id for j in jobs])
    return [
        JobPublic.model_validate(j).model_copy(
            update={"summary": summaries.get(j.job_id)}
        )
        for j in jobs
    ]


@router.get(
    "/{job_id}",
    response_model=JobWithDetailPublic,
    status_code=status.HTTP_200_OK,
)
async def get_job_endpoint(
    job_id: uuid.UUID,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Job:
    """Fetch a job with its aliases + category budgets eager-loaded."""
    try:
        return await get_job(db, job_id)
    except JobNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Job not found"
        ) from exc


@router.get(
    "/{job_id}/budget-summary",
    response_model=JobBudgetSummary,
    status_code=status.HTTP_200_OK,
)
async def get_job_budget_summary_endpoint(
    job_id: uuid.UUID,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> JobBudgetSummary:
    """Per-job actual-vs-budget rollup with per-category breakdown (admin only).

    Phase 3 Lite. The categories list includes every category with
    either a budget row or at least one non-rejected expense on the
    job; categories with neither are omitted.
    """
    try:
        return await summarize_job(db, job_id)
    except JobNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Job not found"
        ) from exc


@router.patch(
    "/{job_id}",
    response_model=JobPublic,
    status_code=status.HTTP_200_OK,
)
async def update_job_endpoint(
    job_id: uuid.UUID,
    body: JobUpdate,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> Job:
    """Partially update a job (admin only).

    PATCH semantics (Phase 3 Lite+ correction):

    * Field omitted from JSON → no change to the column.
    * Field present with explicit ``null`` → clear the column to NULL
      (only valid for nullable columns; the DB CHECK / NOT NULL
      constraints are the backstop for misuse).

    The differentiation is done by ``model_dump(exclude_unset=True)`` —
    Pydantic tracks which fields the caller actually included in the
    request body, separately from the field defaults.

    Constraint-violation mapping:

    * Duplicate ``job_code`` (the ``jobs_job_code_key`` UNIQUE index) →
      409 with friendly detail ``"Job code already exists"`` (Job
      Lifecycle v1A-1; mirrors the POST /jobs hardening at d364abc).
    * Other ``IntegrityError`` causes (e.g. cross-field DB CHECK
      violations Pydantic can't see, like patching ``warning_amber_pct``
      to a value that's no longer strictly less than the stored
      ``warning_red_pct``) → 422 with the raw error detail.

    Job Lifecycle v1A-1: the admin's :class:`User` is forwarded as
    ``actor`` so the service can write a ``job_audit_log`` row when
    any of the auditable fields (``job_name``, ``job_code``,
    ``site_address``, ``status``) actually changes.
    """
    set_fields = body.model_dump(exclude_unset=True)
    try:
        return await update_job(db, job_id, actor=admin, **set_fields)
    except JobNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Job not found"
        ) from exc
    except DuplicateJobCode as exc:
        # v1A-1: pre-checked in the service so we never reach an
        # IntegrityError-during-flush state. Friendly 409 with the
        # same detail string as the POST hardening at d364abc.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Job code already exists",
        ) from exc
    except IntegrityError as exc:
        # Cross-field DB CHECK violation (e.g. ``warning_amber_pct``
        # vs ``warning_red_pct``). Pydantic can't see this; the DB
        # surfaces it as IntegrityError; we translate to 422. We
        # deliberately do NOT call ``db.rollback()`` here — SQLAlchemy
        # auto-rolls back the session on close (FastAPI's request
        # lifecycle handles via ``get_db``). Calling rollback
        # explicitly collides with the test fixture's outer transaction
        # wrapper and surfaces a confusing SAWarning.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Database constraint violated: {exc.orig}",
        ) from exc


@router.get(
    "/{job_id}/audit",
    response_model=list[JobAuditRow],
    status_code=status.HTTP_200_OK,
)
async def get_job_audit_endpoint(
    job_id: uuid.UUID,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Audit trail for a job, newest first (admin only).

    Job Lifecycle v1A-1. Returns rows from ``job_audit_log`` matching
    the supplied job_id, ordered by ``created_at`` DESC. Hard-deleted
    jobs (v1A-3) leave audit rows with ``job_id=NULL`` via FK SET NULL
    cascade; v1A-1 surfaces audit rows for live jobs only — the
    snapshot-based historical lookup pathway is left for v1A-3 to
    wire up.
    """
    try:
        rows = await list_job_audit(db, job_id)
    except JobNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Job not found"
        ) from exc
    return rows


@router.post(
    "/{job_id}/aliases",
    response_model=JobAliasPublic,
    status_code=status.HTTP_201_CREATED,
)
async def add_alias_endpoint(
    job_id: uuid.UUID,
    body: JobAliasCreate,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Attach an alias to a job (admin only).

    409 on a duplicate normalised alias (globally unique — see the
    ``JobAlias`` model's uniqueness contract).
    """
    try:
        return await add_alias(
            db,
            job_id,
            alias_text=body.alias_text,
            language_code=body.language_code,
        )
    except JobNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Job not found"
        ) from exc
    except DuplicateAlias as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Alias with that normalised form already exists",
        ) from exc


@router.post(
    "/{job_id}/category-budgets",
    response_model=JobCategoryBudgetPublic,
    status_code=status.HTTP_201_CREATED,
)
async def add_category_budget_endpoint(
    job_id: uuid.UUID,
    body: JobCategoryBudgetCreate,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Attach a per-category budget row to a job (admin only).

    404 if either the job or the category doesn't exist. 409 on a
    duplicate ``(job_id, category_id)`` pair.
    """
    try:
        return await add_category_budget(
            db,
            job_id,
            category_id=body.category_id,
            budget_amount_ex_gst=body.budget_amount_ex_gst,
        )
    except JobNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Job not found"
        ) from exc
    except CategoryNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Category not found"
        ) from exc
    except DuplicateBudget as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Budget for that job + category already exists",
        ) from exc
