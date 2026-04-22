"""Supplier / supplier-alias HTTP routes.

Thin layer that forwards to :mod:`app.services.suppliers` and maps the
service's domain exceptions onto HTTP status codes:

* :class:`SupplierNotFound` -> 404
* :class:`DuplicateSupplierName` / :class:`DuplicateSupplierAlias` -> 409

Auth policy:

* ``POST`` / ``PATCH`` of suppliers and aliases are all admin-only
  (``Depends(require_admin)``).
* ``GET /suppliers`` is accessible to any authenticated caller. The
  optional ``active_only`` query filter restricts to ``is_active=True``
  rows.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_user, require_admin
from app.models.supplier import Supplier, SupplierAlias
from app.models.user import User
from app.schemas.supplier import (
    SupplierAliasCreate,
    SupplierAliasPublic,
    SupplierCreate,
    SupplierPublic,
    SupplierUpdate,
)
from app.services.suppliers import (
    DuplicateSupplierAlias,
    DuplicateSupplierName,
    SupplierNotFound,
    add_alias,
    create_supplier,
    list_suppliers,
    update_supplier,
)

router = APIRouter(tags=["suppliers"])


@router.get(
    "",
    response_model=list[SupplierPublic],
    status_code=status.HTTP_200_OK,
)
async def list_suppliers_endpoint(
    active_only: bool = False,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[Supplier]:
    """List all suppliers (any authenticated caller).

    With ``?active_only=1`` only ``is_active=True`` rows are returned.
    """
    return await list_suppliers(db, active_only=active_only)


@router.post(
    "",
    response_model=SupplierPublic,
    status_code=status.HTTP_201_CREATED,
)
async def create_supplier_endpoint(
    body: SupplierCreate,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> Supplier:
    """Create a supplier (admin only)."""
    try:
        return await create_supplier(db, body)
    except DuplicateSupplierName as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Supplier with that name already exists",
        ) from exc


@router.patch(
    "/{supplier_id}",
    response_model=SupplierPublic,
    status_code=status.HTTP_200_OK,
)
async def update_supplier_endpoint(
    supplier_id: uuid.UUID,
    body: SupplierUpdate,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> Supplier:
    """Partially update a supplier (admin only)."""
    try:
        return await update_supplier(db, supplier_id, body)
    except SupplierNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Supplier not found"
        ) from exc
    except DuplicateSupplierName as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Supplier with that name already exists",
        ) from exc


@router.post(
    "/{supplier_id}/aliases",
    response_model=SupplierAliasPublic,
    status_code=status.HTTP_201_CREATED,
)
async def add_alias_endpoint(
    supplier_id: uuid.UUID,
    body: SupplierAliasCreate,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> SupplierAlias:
    """Attach an alias to a supplier (admin only).

    409 on a duplicate normalised alias (globally unique — see the
    ``SupplierAlias`` model's uniqueness contract).
    """
    try:
        return await add_alias(
            db,
            supplier_id,
            alias_text=body.alias_text,
            language_code=body.language_code,
        )
    except SupplierNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Supplier not found"
        ) from exc
    except DuplicateSupplierAlias as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Alias with that normalised form already exists",
        ) from exc
