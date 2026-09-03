"""Phase 4 — admin-only reporting endpoints.

V1 shipped the accountant Excel export. The PDF expense report
(founder decision 2026-08-24) adds a second, JSON-shaped route that
feeds the client-rendered report; the Excel export is unchanged and
remains the accountant's export.

Auth: every route in this module requires admin role.
"""

from __future__ import annotations

import uuid
from datetime import date
from io import BytesIO
from urllib.parse import quote as urlquote

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import require_admin
from app.models.job import Job
from app.models.user import User
from app.schemas.expense_report import ExpenseReportData
from app.services.excel_export import (
    build_export_filename,
    build_workbook,
)
from app.services.expense_report import build_report_data
from app.services.jobs import JobNotFound

router = APIRouter(tags=["reports"])

# Excel 2007+ MIME type — same string Microsoft uses for .xlsx.
_XLSX_MIME = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)


def _build_content_disposition(ascii_fallback: str, utf8: str) -> str:
    """Build an RFC 5987 dual-form Content-Disposition value.

    Older clients honour the plain ``filename="..."`` (ASCII-only).
    Modern clients (Chrome / Firefox / Safari / Edge) honour
    ``filename*=UTF-8''<percent-encoded>`` and present that form to
    the user — so a job name like ``晶晶`` survives the HTTP roundtrip.
    """
    return (
        f'attachment; filename="{ascii_fallback}"; '
        f"filename*=UTF-8''{urlquote(utf8, safe='-_.~')}"
    )


@router.get(
    "/expenses-excel",
    status_code=status.HTTP_200_OK,
)
async def get_expenses_excel_endpoint(
    from_date: date | None = Query(default=None),
    to_date: date | None = Query(default=None),
    job_id: uuid.UUID | None = Query(default=None),
    include_pending: bool = Query(default=False),
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """Stream the accountant Excel workbook.

    Default inclusion rule is reviewed-only. Rejected always excluded.
    Pending requires explicit ``include_pending=true`` opt-in — the
    dashboard's "worst case" view is intentionally NOT shipped to the
    accountant by default.

    Date range and ``job_id`` filters apply on top of the inclusion
    rule. See ``docs/phase-4-plan.md`` for the frozen contract.
    """
    if from_date is not None and to_date is not None and from_date > to_date:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="from_date must be on or before to_date",
        )

    # For the single-job filename path we need the job name (for both the
    # ASCII fallback slug and the UTF-8 form). We fetch it here even
    # though build_workbook will re-fetch — keeps build_workbook
    # signature lean (bytes-only) and centralises the filename logic
    # in the endpoint.
    job_name: str | None = None
    if job_id is not None:
        job_row = (
            await db.execute(select(Job.job_name).where(Job.job_id == job_id))
        ).scalar_one_or_none()
        if job_row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Job not found"
            )
        job_name = job_row

    try:
        body = await build_workbook(
            db,
            from_date=from_date,
            to_date=to_date,
            job_id=job_id,
            include_pending=include_pending,
        )
    except JobNotFound as exc:
        # Defensive — pre-check above should catch this; cover the race
        # where the job was deleted between the pre-check and the build.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Job not found"
        ) from exc

    filename = build_export_filename(
        from_date=from_date,
        to_date=to_date,
        job_name=job_name,
        job_id=job_id,
        today=date.today(),
    )

    return StreamingResponse(
        BytesIO(body),
        media_type=_XLSX_MIME,
        headers={
            "Content-Disposition": _build_content_disposition(
                filename.ascii_fallback, filename.utf8
            )
        },
    )


@router.get(
    "/expenses-report",
    response_model=ExpenseReportData,
    status_code=status.HTTP_200_OK,
)
async def get_expenses_report_endpoint(
    from_date: date | None = Query(default=None),
    to_date: date | None = Query(default=None),
    job_id: uuid.UUID | None = Query(default=None),
    include_pending: bool = Query(default=False),
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ExpenseReportData:
    """Report data for the client-rendered PDF expense report.

    Same filters and the same frozen inclusion rule as
    ``/expenses-excel`` (reviewed always; pending on explicit opt-in;
    rejected never) — the two exports share
    :func:`fetch_export_expenses` so they cannot drift apart.

    Every aggregate is computed server-side; the client lays out and
    formats only. Admin-only, matching the Excel export and the wider
    money-visibility posture.
    """
    if from_date is not None and to_date is not None and from_date > to_date:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="from_date must be on or before to_date",
        )
    if job_id is not None:
        exists = (
            await db.execute(select(Job.job_id).where(Job.job_id == job_id))
        ).scalar_one_or_none()
        if exists is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Job not found"
            )

    data = await build_report_data(
        db,
        from_date=from_date,
        to_date=to_date,
        job_id=job_id,
        include_pending=include_pending,
    )
    return ExpenseReportData.model_validate(data)
