"""Org-settings endpoints — admin-only, both directions.

GET is admin-only (not any-auth) on the conservative money-visibility
posture: ``default_day_hours`` is a costing parameter, and contributors
never see cost math inputs (mirrors the ``hourly_rate`` strip on
``/workers``). The mobile client only needs it for the admin settings
row in the Labour tab; contributor screens never fetch it.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import require_admin
from app.models.user import User
from app.schemas.org_settings import OrgSettingsRead, OrgSettingsUpdate
from app.services import org_settings as svc

router = APIRouter()


@router.get(
    "/org-settings",
    response_model=OrgSettingsRead,
    status_code=status.HTTP_200_OK,
)
async def get_org_settings_endpoint(
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> OrgSettingsRead:
    row = await svc.get_org_settings(db)
    return OrgSettingsRead.model_validate(row)


@router.patch(
    "/org-settings",
    response_model=OrgSettingsRead,
    status_code=status.HTTP_200_OK,
)
async def update_org_settings_endpoint(
    payload: OrgSettingsUpdate,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> OrgSettingsRead:
    row = await svc.update_org_settings(
        db, default_day_hours=payload.default_day_hours
    )
    return OrgSettingsRead.model_validate(row)
