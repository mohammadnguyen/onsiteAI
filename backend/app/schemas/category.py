"""Public-facing category schemas.

``CategoryPublic`` is the canonical wire shape. ``CategoryCreate`` is the
inbound body for admin-only ``POST /categories``. ``CategoryUpdate`` is
the inbound body for admin-only ``PATCH /categories/{id}`` — every field
is optional so partial updates don't force callers to round-trip values.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field


class CategoryPublic(BaseModel):
    """Serialised view of a :class:`~app.models.category.Category` row."""

    model_config = ConfigDict(from_attributes=True)

    category_id: uuid.UUID
    category_name: str
    display_order: int
    is_active: bool


class CategoryCreate(BaseModel):
    """Body of ``POST /categories`` (admin-only)."""

    category_name: str = Field(min_length=1, max_length=100)
    display_order: int = Field(ge=0)
    is_active: bool = True


class CategoryUpdate(BaseModel):
    """Body of ``PATCH /categories/{category_id}`` (admin-only)."""

    category_name: str | None = Field(default=None, min_length=1, max_length=100)
    display_order: int | None = Field(default=None, ge=0)
    is_active: bool | None = None
