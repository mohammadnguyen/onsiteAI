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

import logging
import uuid
from typing import TypeVar

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_user, require_admin
from app.models.job import Job
from app.models.user import User, UserRole
from app.schemas.budget_summary import JobBudgetSummary
from app.schemas.job import (
    JobAliasCreate,
    JobAliasPublic,
    JobAuditRow,
    JobCategoryBudgetCreate,
    JobCategoryBudgetPublic,
    JobCategoryBudgetUpdate,
    JobCreate,
    JobPublic,
    JobUpdate,
    JobWithDetailPublic,
)
from app.services.budget_summary import summarize_job, summarize_jobs
from app.services.jobs import (
    BudgetNotFound,
    CategoryNotFound,
    DuplicateAlias,
    DuplicateBudget,
    DuplicateJobCode,
    JobHasDependencies,
    JobNotFound,
    add_alias,
    add_category_budget,
    create_job,
    delete_category_budget,
    delete_empty_job,
    get_job,
    list_job_audit,
    list_jobs,
    update_category_budget,
    update_job,
)

router = APIRouter(tags=["jobs"])

# Audit E4: raw driver error text (column/constraint names, row-value
# fragments) must not cross the API boundary. Log it server-side; return a
# generic 422 to the client.
_errlog = logging.getLogger("app.errors")


def _constraint_violation(exc: IntegrityError) -> HTTPException:
    _errlog.warning("job_constraint_violation orig=%s", exc.orig)
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail="Request violates a data constraint",
    )


_JobPublicT = TypeVar("_JobPublicT", bound=JobPublic)


def _strip_contributor_money(public: _JobPublicT, user: User) -> _JobPublicT:
    """Null the admin-only money fields for non-admin callers.

    Jobs mirror of the O1-S1 expense strip
    (:func:`app.api.expenses._strip_contributor_money`): operates on a
    COPY of the response model via ``model_copy`` — never the ORM row —
    so stored jobs are untouched. Contributors keep job IDENTITY
    (``job_id``, ``job_code``, ``job_name``, ``site_address``,
    ``status``, ``aliases``) — the mobile capture job picker depends on
    those — but never receive ``contract_value_ex_gst``,
    ``total_budget_ex_gst``, ``target_profit_ratio_pct``, the warning
    thresholds, the embedded budget ``summary``, or per-category budget
    rows. Admins get the object unchanged. This is API response shaping
    only — NOT a DB schema change (every stripped field is already
    nullable on the wire).

    ``gst_mode`` is deliberately NOT stripped: it is a required
    (non-nullable) field on the wire shape both clients type against,
    and without any amounts it discloses no financial figure. Flagged
    for the operator in the review rather than silently changed.
    """
    if user.role == UserRole.admin:
        return public
    update: dict[str, object] = {
        "contract_value_ex_gst": None,
        "total_budget_ex_gst": None,
        "target_profit_ratio_pct": None,
        "warning_amber_pct": None,
        "warning_red_pct": None,
        "summary": None,
    }
    if isinstance(public, JobWithDetailPublic):
        update["category_budgets"] = []
    return public.model_copy(update=update)


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
            gst_mode=body.gst_mode,
        )
    except IntegrityError as exc:
        err_text = str(exc.orig).lower()
        if "unique" in err_text or "duplicate key" in err_text:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Job code already exists",
            ) from exc
        raise _constraint_violation(exc) from exc


@router.get(
    "",
    response_model=list[JobPublic],
    status_code=status.HTTP_200_OK,
)
async def list_jobs_endpoint(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[JobPublic]:
    """List all jobs (any authenticated caller; contributors get identity only).

    Phase 3 Lite: each ADMIN row carries a ``summary`` field with the
    per-job ex-GST aggregates used by the dashboard. Jobs with no
    expenses get an all-zero summary so the UI never has to
    special-case missing data.

    Jobs money strip (post-O1-S1): contributor responses carry job
    identity only — contract/budget/margin/threshold fields and the
    ``summary`` are nulled server-side (previously this was a
    client-side hide only). The money aggregation is skipped entirely
    for contributors rather than computed-then-stripped.
    """
    jobs = await list_jobs(db)
    if user.role != UserRole.admin:
        return [
            _strip_contributor_money(JobPublic.model_validate(j), user)
            for j in jobs
        ]
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
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> JobWithDetailPublic:
    """Fetch a job with its aliases eager-loaded.

    Admins additionally receive the money fields + ``category_budgets``;
    contributor responses have those stripped server-side (jobs money
    strip — aliases stay, the mobile capture job picker reads them).
    """
    try:
        job = await get_job(db, job_id)
    except JobNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Job not found"
        ) from exc
    return _strip_contributor_money(
        JobWithDetailPublic.model_validate(job), user
    )


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
        raise _constraint_violation(exc) from exc


@router.delete(
    "/{job_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_job_endpoint(
    job_id: uuid.UUID,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Hard-delete an EMPTY job (admin only).

    Job Lifecycle v1A-3. Allowed only when the job has zero expenses
    and zero review-queue rows. Aliases + per-category budgets cascade-
    delete via the existing model FK config; the audit row written
    just before the SQL DELETE survives via ``SET NULL`` on
    ``job_audit_log.job_id`` (v1A-1 design).

    No ``reason`` query param is accepted: v1A-3 chose R1=Option B
    (no reason input anywhere) because the audit table has no
    ``reason`` column. Accepting a value the system silently dropped
    would mislead callers. When a ``reason`` column lands in a
    future schema change, this endpoint + the service signature can
    be extended in the same batch.

    Status codes:

    * **204** — deleted; aliases + budgets cascaded; audit row
      persisted (queryable via direct DB inspection only — the
      ``GET /jobs/{id}/audit`` endpoint still returns 404 for the
      removed id in v1A-3; the snapshot-based historical lookup
      pathway is intentionally deferred per the v1A-3 R2 decision).
    * **409** — blocked by a dependency (carries the
      ``JobHasDependencies.detail`` verbatim, e.g.
      "Job has 3 expenses and cannot be deleted. Archive it
      instead.").
    * **404** — job_id does not resolve.
    """
    try:
        await delete_empty_job(db, admin=admin, job_id=job_id)
    except JobNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Job not found"
        ) from exc
    except JobHasDependencies as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=exc.detail
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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


@router.patch(
    "/{job_id}/category-budgets/{budget_id}",
    response_model=JobCategoryBudgetPublic,
    status_code=status.HTTP_200_OK,
)
async def update_category_budget_endpoint(
    job_id: uuid.UUID,
    budget_id: uuid.UUID,
    body: JobCategoryBudgetUpdate,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Update a per-category budget row's amount (admin only).

    Slice A. 404 if ``(job_id, budget_id)`` doesn't resolve — either
    the budget doesn't exist OR it belongs to a different job. The
    pair is checked atomically so a mismatched job_id cannot
    accidentally update someone else's budget.

    Pydantic rejects negative amounts at the schema layer
    (:class:`~app.schemas.job.JobCategoryBudgetUpdate` ``ge=0``),
    surfacing as 422 before this handler is reached. ``0`` is
    explicitly permitted (a zero budget is a valid statement).
    """
    try:
        return await update_category_budget(
            db,
            job_id,
            budget_id,
            budget_amount_ex_gst=body.budget_amount_ex_gst,
        )
    except BudgetNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Budget not found",
        ) from exc


@router.delete(
    "/{job_id}/category-budgets/{budget_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_category_budget_endpoint(
    job_id: uuid.UUID,
    budget_id: uuid.UUID,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Delete a per-category budget row (admin only).

    Slice A. 404 if ``(job_id, budget_id)`` doesn't resolve — same
    no-leak rationale as the PATCH endpoint. A second DELETE on the
    same ``budget_id`` also returns 404 (not silently 204); callers
    that want noop-on-missing semantics should ignore the 404
    themselves.
    """
    try:
        await delete_category_budget(db, job_id, budget_id)
    except BudgetNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Budget not found",
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
