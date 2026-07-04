"""Labour v1 HTTP routes (slice L-A).

Thin layer over :mod:`app.services.labour`, mapping domain exceptions:

* :class:`WorkerNotFound` / :class:`LabourEntryNotFound` -> 404
* :class:`LabourValidationError` -> 422
* :class:`LabourEditForbidden` -> 403

One router file spans three URL roots (``/workers``,
``/labour-entries``, ``/labour-summary``) because they are one
feature; the router is therefore included WITHOUT a prefix in
:mod:`app.api.router` and declares absolute paths — the single
deliberate deviation from the per-resource-prefix convention.

Auth policy (operator decisions OD-1..3):

* Worker roster: READ any authenticated caller (the tick screen needs
  it); WRITE admin-only. No delete route exists — workers deactivate.
* Attendance: create/batch any authenticated caller; edit/delete =
  admin always, contributor own+today only (enforced in the service);
  READS any authenticated caller (site presence, not money).
* Summary: admin-only (it informs payment decisions). A separate
  per-job ROLLUP (``/labour-rollup``) is contributor-safe — labourers /
  worker-days / days-on-site for everyone; hours + cost stripped to null
  for non-admins server-side, so summary auth never has to be loosened.
"""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_user, require_admin
from app.models.user import User, UserRole
from app.schemas.labour import (
    JobLabourRollup,
    LabourBatchRequest,
    LabourEntryPublic,
    LabourSummary,
    WorkerCreate,
    WorkerPublic,
    WorkerUpdate,
)
from app.services import labour as svc

router = APIRouter(tags=["labour"])


@router.get(
    "/workers",
    response_model=list[WorkerPublic],
    status_code=status.HTTP_200_OK,
)
async def list_workers_endpoint(
    include_inactive: bool = False,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[WorkerPublic]:
    """Roster list (any authenticated caller). Active-only by default.

    ``hourly_rate`` is ADMIN-ONLY — it is stripped to null for
    contributors so pay rates never reach a non-admin device (the tick
    screen needs names, not rates).
    """
    rows = await svc.list_workers(db, include_inactive=include_inactive)
    is_admin = _user.role == UserRole.admin
    result: list[WorkerPublic] = []
    for w in rows:
        pub = WorkerPublic.model_validate(w)
        if not is_admin:
            pub.hourly_rate = None
        result.append(pub)
    return result


@router.post(
    "/workers",
    response_model=WorkerPublic,
    status_code=status.HTTP_201_CREATED,
)
async def create_worker_endpoint(
    body: WorkerCreate,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> WorkerPublic:
    """Add a worker to the roster (admin only). Duplicate names allowed."""
    worker = await svc.create_worker(
        db,
        created_by=admin,
        display_name=body.display_name,
        note=body.note,
        hourly_rate=body.hourly_rate,
    )
    return WorkerPublic.model_validate(worker)


@router.patch(
    "/workers/{worker_id}",
    response_model=WorkerPublic,
    status_code=status.HTTP_200_OK,
)
async def update_worker_endpoint(
    worker_id: uuid.UUID,
    body: WorkerUpdate,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> WorkerPublic:
    """Rename / annotate / (de)activate a worker (admin only)."""
    set_fields = body.model_dump(exclude_unset=True)
    try:
        worker = await svc.update_worker(db, worker_id, **set_fields)
    except svc.WorkerNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Worker not found"
        ) from exc
    return WorkerPublic.model_validate(worker)


@router.post(
    "/labour-entries/batch",
    response_model=list[LabourEntryPublic],
    status_code=status.HTTP_201_CREATED,
)
async def batch_entries_endpoint(
    body: LabourBatchRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[LabourEntryPublic]:
    """The tick-screen save: all-or-nothing create-or-update."""
    try:
        rows = await svc.batch_upsert_entries(
            db,
            current_user=current_user,
            job_id=body.job_id,
            work_date=body.work_date,
            items=body.entries,
        )
    except svc.LabourValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.detail) from exc
    except svc.LabourEditForbidden as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=exc.detail
        ) from exc
    return [LabourEntryPublic.model_validate(r) for r in rows]


@router.get(
    "/labour-entries",
    response_model=list[LabourEntryPublic],
    status_code=status.HTTP_200_OK,
)
async def list_entries_endpoint(
    job_id: uuid.UUID | None = None,
    worker_id: uuid.UUID | None = None,
    from_date: date | None = Query(default=None, alias="from"),
    to_date: date | None = Query(default=None, alias="to"),
    limit: int = Query(default=200, ge=1, le=500),
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[LabourEntryPublic]:
    """Attendance entries, newest first (any authenticated caller)."""
    rows = await svc.list_entries(
        db,
        job_id=job_id,
        worker_id=worker_id,
        from_date=from_date,
        to_date=to_date,
        limit=limit,
    )
    return [LabourEntryPublic.model_validate(r) for r in rows]


@router.delete(
    "/labour-entries/{entry_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_entry_endpoint(
    entry_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Remove one entry (admin any; contributor own + today's date)."""
    try:
        await svc.delete_entry(db, current_user=current_user, entry_id=entry_id)
    except svc.LabourEntryNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Labour entry not found"
        ) from exc
    except svc.LabourEditForbidden as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=exc.detail
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/labour-summary",
    response_model=LabourSummary,
    status_code=status.HTTP_200_OK,
)
async def labour_summary_endpoint(
    from_date: date | None = Query(default=None, alias="from"),
    to_date: date | None = Query(default=None, alias="to"),
    job_id: uuid.UUID | None = None,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> LabourSummary:
    """Fortnight attendance summary source (admin only): per-worker and
    per-job day totals for the filtered range."""
    summary = await svc.summarize(
        db, from_date=from_date, to_date=to_date, job_id=job_id
    )
    return LabourSummary(**summary)


@router.get(
    "/labour-rollup",
    response_model=list[JobLabourRollup],
    status_code=status.HTTP_200_OK,
)
async def labour_rollup_endpoint(
    from_date: date | None = Query(default=None, alias="from"),
    to_date: date | None = Query(default=None, alias="to"),
    job_id: uuid.UUID | None = None,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[JobLabourRollup]:
    """Contributor-safe per-job labour rollup (any authenticated caller).

    Returns ``labourers`` / ``worker_days`` / ``days_on_site`` for EVERY
    role. ``total_hours`` and ``labour_cost`` are populated for ADMINS
    ONLY and stripped to null for contributors server-side (mirroring
    the ``/workers`` hourly_rate strip in
    :func:`list_workers_endpoint`). ``hourly_rate`` is never in this
    shape.

    The admin-only ``/labour-summary`` route — which also carries
    per-worker cost — is intentionally left unchanged. This is a
    separate, narrower endpoint precisely so loosening summary auth (and
    leaking per-worker money) is never required to give contributors a
    money-free per-job view.
    """
    summary = await svc.summarize(
        db, from_date=from_date, to_date=to_date, job_id=job_id
    )
    is_admin = _user.role == UserRole.admin
    return [
        JobLabourRollup(
            job_id=j["job_id"],
            job_name=j["job_name"],
            labourers=j["labourers"],
            worker_days=j["total_days"],
            days_on_site=j["days_on_site"],
            total_hours=j["total_hours"] if is_admin else None,
            labour_cost=j["labour_cost"] if is_admin else None,
        )
        for j in summary["jobs"]
    ]
