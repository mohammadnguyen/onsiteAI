"""Supplier-related business logic. HTTP-agnostic; raises domain exceptions.

Each function takes an :class:`AsyncSession` plus typed inputs and either
returns a persisted model or raises one of the domain exceptions defined
at the top of this module. The HTTP layer (``app/api/suppliers.py``) is
the only caller and is responsible for mapping these exceptions onto the
correct status codes.

Duplicate checks (supplier name, alias) are performed as a pre-SELECT
inside the same transaction rather than relying on the DB's UNIQUE
constraint to raise. This follows the same SAVEPOINT-hygiene rationale as
:mod:`app.services.jobs`: under pytest's rollback-on-teardown transaction
a failed INSERT would otherwise poison the enclosing SAVEPOINT. The
UNIQUE constraint remains the real backstop for the race window.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.text import normalize_alias
from app.models.supplier import Supplier, SupplierAlias
from app.models.user import LanguageCode
from app.schemas.supplier import SupplierCreate, SupplierUpdate


class SupplierNotFound(Exception):
    """Raised when a supplier_id doesn't resolve to a persisted row."""

    def __init__(self, supplier_id: uuid.UUID):
        self.supplier_id = supplier_id
        super().__init__(f"Supplier {supplier_id} not found")


class DuplicateSupplierName(Exception):
    """Raised when a supplier's normalised name already exists."""

    def __init__(self, supplier_normalized: str):
        self.supplier_normalized = supplier_normalized
        super().__init__(f"Supplier with normalized name {supplier_normalized!r} already exists")


class DuplicateSupplierAlias(Exception):
    """Raised when a supplier alias's normalised form already exists (any supplier)."""

    def __init__(self, alias_text_normalized: str):
        self.alias_text_normalized = alias_text_normalized
        super().__init__(f"Supplier alias {alias_text_normalized!r} already exists")


async def list_suppliers(db: AsyncSession, *, active_only: bool = False) -> list[Supplier]:
    """Return all suppliers ordered by ``supplier_name``.

    When ``active_only`` is ``True`` only rows with ``is_active=True``
    are returned.
    """
    stmt = select(Supplier).order_by(Supplier.supplier_name)
    if active_only:
        stmt = stmt.where(Supplier.is_active.is_(True))
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_supplier(db: AsyncSession, supplier_id: uuid.UUID) -> Supplier:
    """Fetch one supplier by id.

    Raises :class:`SupplierNotFound` if the id doesn't match.
    """
    supplier = await db.get(Supplier, supplier_id)
    if supplier is None:
        raise SupplierNotFound(supplier_id)
    return supplier


async def create_supplier(db: AsyncSession, data: SupplierCreate) -> Supplier:
    """Insert a new :class:`Supplier`.

    Pre-checks the normalised form of ``supplier_name`` against the
    globally-unique ``supplier_normalized`` column and raises
    :class:`DuplicateSupplierName` on a collision. The model's
    ``before_insert`` event listener populates ``supplier_normalized`` so
    callers don't set it directly.
    """
    normalized = normalize_alias(data.supplier_name)
    existing = (
        await db.execute(select(Supplier).where(Supplier.supplier_normalized == normalized))
    ).scalar_one_or_none()
    if existing is not None:
        raise DuplicateSupplierName(normalized)

    supplier = Supplier(
        supplier_id=uuid.uuid4(),
        supplier_name=data.supplier_name,
        is_active=data.is_active,
    )
    db.add(supplier)
    await db.flush()
    return supplier


async def update_supplier(
    db: AsyncSession, supplier_id: uuid.UUID, data: SupplierUpdate
) -> Supplier:
    """Partial update; only non-``None`` fields are applied.

    Raises :class:`SupplierNotFound` on a missing id and
    :class:`DuplicateSupplierName` if renaming would collide with another
    supplier's normalised name. The model's ``before_update`` listener
    re-syncs ``supplier_normalized`` when ``supplier_name`` changes.
    """
    supplier = await get_supplier(db, supplier_id)

    if data.supplier_name is not None and data.supplier_name != supplier.supplier_name:
        normalized = normalize_alias(data.supplier_name)
        clash = (
            await db.execute(
                select(Supplier).where(
                    Supplier.supplier_normalized == normalized,
                    Supplier.supplier_id != supplier_id,
                )
            )
        ).scalar_one_or_none()
        if clash is not None:
            raise DuplicateSupplierName(normalized)
        supplier.supplier_name = data.supplier_name

    if data.is_active is not None:
        supplier.is_active = data.is_active

    await db.flush()
    return supplier


async def add_alias(
    db: AsyncSession,
    supplier_id: uuid.UUID,
    *,
    alias_text: str,
    language_code: LanguageCode | None = None,
) -> SupplierAlias:
    """Create a :class:`SupplierAlias` under ``supplier_id``.

    Raises :class:`SupplierNotFound` if the parent supplier doesn't
    exist and :class:`DuplicateSupplierAlias` if the normalised form is
    already claimed (globally, not per-supplier — see model docstring).
    """
    # 404 before 409 so callers get the more specific error first.
    _ = await get_supplier(db, supplier_id)

    normalized = normalize_alias(alias_text)
    existing = (
        await db.execute(
            select(SupplierAlias).where(SupplierAlias.alias_text_normalized == normalized)
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise DuplicateSupplierAlias(normalized)

    alias = SupplierAlias(
        alias_id=uuid.uuid4(),
        supplier_id=supplier_id,
        alias_text=alias_text,
        language_code=language_code,
    )
    db.add(alias)
    await db.flush()
    return alias
