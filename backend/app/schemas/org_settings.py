"""Pydantic shapes for the org-settings endpoints (admin-only).

``default_day_hours`` is a Decimal string in transit (house money/
quantity convention — the client formats, never computes).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


class OrgSettingsRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    default_day_hours: Decimal


class OrgSettingsUpdate(BaseModel):
    """PATCH body — the one adjustable field, bounded like the DB CHECK."""

    default_day_hours: Annotated[
        Decimal,
        Field(gt=Decimal("0"), le=Decimal("24"), decimal_places=2),
    ]
