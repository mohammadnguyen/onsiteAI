"""Phase 2 Task T-A: model-level tests for ``Supplier`` / ``SupplierAlias``.

Mirrors the style of ``tests/test_job_model.py``. Exercises:

* the ``before_insert`` listener that populates ``supplier_normalized``
* the same listener firing on ``before_update`` so the normalised form
  tracks the canonical name
* the ``before_insert`` listener that populates ``alias_text_normalized``
* the global UNIQUE on normalised aliases — two suppliers cannot both
  claim the same normalised form (parser ambiguity prevention)
* the global UNIQUE on ``supplier_normalized`` — two suppliers cannot
  share a normalised name
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import LanguageCode, Supplier, SupplierAlias


async def _make_supplier(db_session, *, name: str) -> Supplier:
    supplier = Supplier(supplier_id=uuid.uuid4(), supplier_name=name)
    db_session.add(supplier)
    await db_session.flush()
    return supplier


@pytest.mark.asyncio
async def test_supplier_name_auto_normalizes_on_insert(db_session):
    """The ``before_insert`` listener sets ``supplier_normalized`` from ``supplier_name``."""
    supplier = Supplier(supplier_id=uuid.uuid4(), supplier_name="Bunnings Warehouse")
    db_session.add(supplier)
    await db_session.flush()

    assert supplier.supplier_normalized == "bunningswarehouse"


@pytest.mark.asyncio
async def test_supplier_name_reactive_on_update(db_session):
    """Updating ``supplier_name`` re-runs normalisation on flush."""
    supplier = await _make_supplier(db_session, name="Bunnings Warehouse")
    assert supplier.supplier_normalized == "bunningswarehouse"

    supplier.supplier_name = "Reece Plumbing"
    await db_session.flush()

    assert supplier.supplier_normalized == "reeceplumbing"


@pytest.mark.asyncio
async def test_alias_auto_normalizes_on_insert(db_session):
    """The ``before_insert`` listener sets ``alias_text_normalized`` from ``alias_text``."""
    supplier = await _make_supplier(db_session, name="Bunnings Warehouse")

    alias = SupplierAlias(
        supplier_id=supplier.supplier_id,
        alias_text="BWC Store",
        language_code=LanguageCode.en,
    )
    db_session.add(alias)
    await db_session.flush()

    assert alias.alias_text_normalized == "bwcstore"


@pytest.mark.asyncio
async def test_duplicate_normalized_alias_across_suppliers_raises(db_session):
    """Two suppliers cannot both claim the same normalised alias."""
    supplier_a = await _make_supplier(db_session, name="Supplier Alpha")
    supplier_b = await _make_supplier(db_session, name="Supplier Beta")

    db_session.add(SupplierAlias(supplier_id=supplier_a.supplier_id, alias_text="Bunnings"))
    await db_session.flush()

    # Use a SAVEPOINT so the expected IntegrityError only poisons the
    # inner block, not the outer rollback-on-teardown transaction.
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            db_session.add(SupplierAlias(supplier_id=supplier_b.supplier_id, alias_text="BUNNINGS"))
            await db_session.flush()


@pytest.mark.asyncio
async def test_duplicate_normalized_supplier_name_raises(db_session):
    """Two suppliers whose names normalise identically cannot coexist."""
    await _make_supplier(db_session, name="Bunnings Warehouse")

    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            db_session.add(
                Supplier(
                    supplier_id=uuid.uuid4(),
                    supplier_name="BUNNINGS-WAREHOUSE",
                )
            )
            await db_session.flush()
