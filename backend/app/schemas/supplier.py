"""Public-facing supplier / supplier-alias schemas.

``SupplierPublic`` is the canonical wire shape for ``GET /suppliers`` (list)
and for create/update responses. ``SupplierAliasPublic`` is returned
standalone from the POST nested-alias endpoint.

``SupplierCreate`` / ``SupplierUpdate`` / ``SupplierAliasCreate`` are the
inbound body shapes. Everything on ``SupplierUpdate`` is optional so
partial updates don't force callers to round-trip values they don't want
to touch (same convention as ``CategoryUpdate`` / ``JobUpdate``).
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field

from app.models import LanguageCode


class SupplierPublic(BaseModel):
    """Serialised view of a :class:`~app.models.supplier.Supplier` row."""

    model_config = ConfigDict(from_attributes=True)

    supplier_id: uuid.UUID
    supplier_name: str
    supplier_normalized: str
    is_active: bool


class SupplierCreate(BaseModel):
    """Body of ``POST /suppliers`` (admin-only)."""

    supplier_name: str = Field(min_length=1, max_length=255)
    is_active: bool = True


class SupplierUpdate(BaseModel):
    """Body of ``PATCH /suppliers/{supplier_id}`` (admin-only)."""

    supplier_name: str | None = Field(default=None, min_length=1, max_length=255)
    is_active: bool | None = None


class SupplierAliasPublic(BaseModel):
    """Serialised view of a :class:`~app.models.supplier.SupplierAlias`."""

    model_config = ConfigDict(from_attributes=True)

    alias_id: uuid.UUID
    supplier_id: uuid.UUID
    alias_text: str
    alias_text_normalized: str
    language_code: LanguageCode | None


class SupplierAliasCreate(BaseModel):
    """Body of ``POST /suppliers/{supplier_id}/aliases`` (admin-only)."""

    alias_text: str = Field(min_length=1, max_length=255)
    language_code: LanguageCode | None = None
