"""Potential Variation endpoints (DEC-PV-001: a human flag on a Site Log
entry, for the builder to check).

Routes (absolute paths, included unprefixed like Site Log and tasks,
because two of them live under ``/site-log-events``):

* ``POST  /site-log-events/{event_id}/potential-variations`` — flag (201) or
  replay (200).
* ``GET   /site-log-events/{event_id}/potential-variations`` — visible flags
  from one entry.
* ``GET   /potential-variations`` — visible flags; filters;
  ``create_client_id`` lookup.
* ``GET   /potential-variations/{pv_id}`` — detail, with the pinned version.
* ``PATCH /potential-variations/{pv_id}`` — the builder's decision, the job.

Routes map domain exceptions to status codes and hold no business or
transaction logic. Not visible → 404; visible but not allowed → 403.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_user
from app.models.potential_variation import PvReviewState
from app.models.user import User
from app.schemas.potential_variation import (
    PvAttachmentOut,
    PvCreateIn,
    PvDetailOut,
    PvJobOut,
    PvOut,
    PvPatchIn,
    PvPermissionsOut,
    PvPinnedRevisionOut,
    PvSourceDetailOut,
    PvSourceOut,
    PvUserOut,
)
from app.services import potential_variations as pv_svc

router = APIRouter(tags=["potential-variations"])


def _user_out(user: User | None) -> PvUserOut | None:
    if user is None:
        return None
    return PvUserOut(user_id=user.user_id, full_name=user.full_name)


def _fields(view: pv_svc.PvView) -> dict:
    p = view.pv
    job = view.job
    perms = view.permissions
    return dict(
        potential_variation_id=p.potential_variation_id,
        title=view.title,
        review_state=p.review_state,
        ended_reason=p.ended_reason,
        decision_note=view.decision_note,
        origin=p.origin,
        job=(
            PvJobOut(
                job_id=job.job_id,
                job_name=job.job_name,
                job_code=job.job_code,
                status=job.status,
            )
            if job is not None
            else None
        ),
        created_by=_user_out(view.creator),
        decided_by=_user_out(view.decided_by),
        decided_at=p.decided_at,
        permissions=PvPermissionsOut(
            decide=perms.decide,
            read_note=perms.read_note,
            set_job=perms.set_job,
            change_job=perms.change_job,
        ),
        created_at=p.created_at,
        updated_at=p.updated_at,
    )


def _source(view: pv_svc.PvView) -> dict:
    return dict(
        site_log_event_id=view.pv.source_site_log_event_id,
        revision_no=view.pv.source_revision_no,
        readable=view.source_readable,
        job_id=view.source_job_id,
    )


def _out(view: pv_svc.PvView) -> PvOut:
    return PvOut(**_fields(view), source=PvSourceOut(**_source(view)))


def _detail_out(view: pv_svc.PvView) -> PvDetailOut:
    d = view.detail
    pinned = d.pinned if d is not None else None
    return PvDetailOut(
        **_fields(view),
        source=PvSourceDetailOut(
            **_source(view),
            pinned=(
                PvPinnedRevisionOut(
                    revision_no=pinned.revision_no,
                    body_text=pinned.body_text,
                    internal_location=pinned.internal_location,
                    occurred_at=pinned.occurred_at,
                    created_at=pinned.created_at,
                )
                if pinned is not None
                else None
            ),
            current_revision_no=d.current_revision_no if d is not None else None,
            current_withdrawn=d.current_withdrawn if d is not None else None,
            current_attachments=(
                [
                    PvAttachmentOut(declared_media_type=a.declared_media_type, state=a.state)
                    for a in d.attachments
                ]
                if d is not None and d.attachments is not None
                else None
            ),
        ),
    )


_STATUS = {
    pv_svc.PvNotFound: 404,
    pv_svc.PvSourceNotFound: 404,
    pv_svc.PvJobNotFound: 404,
    pv_svc.PvSourceWithdrawn: 409,
    pv_svc.PvSourceChanged: 409,
    pv_svc.PvJobChanged: 409,
    pv_svc.PvFingerprintMismatch: 409,
    pv_svc.PvForbidden: 403,
    pv_svc.PvJobCompleted: 422,
    pv_svc.PvReasonRequired: 422,
    pv_svc.PvValidationError: 422,
}


def _map(exc: pv_svc.PvError) -> HTTPException:
    for cls, code in _STATUS.items():
        if isinstance(exc, cls):
            return HTTPException(status_code=code, detail=exc.message)
    return HTTPException(status_code=500, detail="Potential variation error")


@router.post(
    "/site-log-events/{event_id}/potential-variations", response_model=PvOut
)
async def create_potential_variation(
    event_id: uuid.UUID,
    payload: PvCreateIn,
    response: Response,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        result = await pv_svc.create_pv(
            db,
            user=user,
            event_id=event_id,
            create_client_id=payload.create_client_id,
            title=payload.title,
            source_revision_no=payload.source_revision_no,
            job_id=payload.job_id,
        )
    except pv_svc.PvError as exc:
        raise _map(exc) from exc
    response.status_code = (
        status.HTTP_201_CREATED if result.created else status.HTTP_200_OK
    )
    return _out(result.view)


@router.get(
    "/site-log-events/{event_id}/potential-variations", response_model=list[PvOut]
)
async def list_event_potential_variations(
    event_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        return [_out(v) for v in await pv_svc.list_event_pvs(db, user, event_id)]
    except pv_svc.PvError as exc:
        raise _map(exc) from exc


@router.get("/potential-variations", response_model=list[PvOut])
async def list_potential_variations(
    review_state: PvReviewState | None = Query(None),
    job_id: uuid.UUID | None = Query(None),
    no_job: bool = Query(False),
    create_client_id: uuid.UUID | None = Query(None),
    limit: int = Query(pv_svc.PV_PAGE_DEFAULT, ge=1, le=pv_svc.PV_PAGE_MAX),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        views = await pv_svc.list_pvs(
            db,
            user,
            review_state=review_state,
            job_id=job_id,
            no_job=no_job,
            create_client_id=create_client_id,
            limit=limit,
            offset=offset,
        )
    except pv_svc.PvError as exc:
        raise _map(exc) from exc
    return [_out(v) for v in views]


@router.get("/potential-variations/{pv_id}", response_model=PvDetailOut)
async def get_potential_variation(
    pv_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        return _detail_out(await pv_svc.get_pv(db, user, pv_id))
    except pv_svc.PvError as exc:
        raise _map(exc) from exc


@router.patch("/potential-variations/{pv_id}", response_model=PvDetailOut)
async def update_potential_variation(
    pv_id: uuid.UUID,
    payload: PvPatchIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    sent = payload.model_fields_set
    decision = None
    if "decision" in sent and payload.decision is not None:
        decision = pv_svc.Decision(
            review_state=payload.decision.review_state,
            ended_reason=payload.decision.ended_reason,
            note=payload.decision.note,
        )
    try:
        view = await pv_svc.update_pv(
            db,
            user=user,
            pv_id=pv_id,
            decision=decision,
            job_id_sent="job_id" in sent,
            job_id=payload.job_id,
            reason=payload.reason,
        )
    except pv_svc.PvError as exc:
        raise _map(exc) from exc
    return _detail_out(view)
