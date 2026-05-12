"""Capture Hardening Patch CHP-6: tests for ``seed_suppliers``.

The seed function ships an AU residential starter supplier set so that
day-one captures of "Bunnings $440 cement" can match a real supplier
instead of bricking the review queue with ``supplier_uncertain``. The
contract:

* Idempotent — re-running produces no duplicates.
* Non-destructive — pre-existing supplier rows are never modified
  (admins may have deactivated/edited a row they don't want).
* Inserts every name in ``STARTER_SUPPLIERS`` that doesn't already
  exist by normalised key.
* The parser's natural-language path resolves a seeded supplier name
  (e.g. ``"Bunnings"``) without an alias row — exercises the
  end-to-end "seeded supplier reduces review-queue volume" outcome.

All tests run against the isolated ``sitetracker_test`` DB via the
project's ``db_session`` fixture (transaction-per-test, rolled back on
teardown). The seed function is NEVER invoked against the live DB.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.core.seed import STARTER_SUPPLIERS, seed_suppliers
from app.core.text import normalize_alias
from app.models.supplier import Supplier
from app.services.parser.suppliers import match_supplier
from app.services.parser.tokens import tokenize


# ---------------------------------------------------------------------------
# Idempotency + insert behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_seed_suppliers_inserts_every_starter_name(db_session):
    """First run on an empty DB inserts one row per name in the starter list."""
    result = await seed_suppliers(db_session)

    assert len(result) == len(STARTER_SUPPLIERS)
    names_inserted = {s.supplier_name for s in result}
    assert names_inserted == set(STARTER_SUPPLIERS)

    # Confirm against the DB, not just the returned list.
    persisted = (await db_session.execute(select(Supplier))).scalars().all()
    assert {s.supplier_name for s in persisted} == set(STARTER_SUPPLIERS)


@pytest.mark.asyncio
async def test_seed_suppliers_is_idempotent_no_duplicates(db_session):
    """Running the seed twice leaves the row count at exactly len(STARTER_SUPPLIERS)."""
    await seed_suppliers(db_session)
    await seed_suppliers(db_session)

    rows = (await db_session.execute(select(Supplier))).scalars().all()
    assert len(rows) == len(STARTER_SUPPLIERS)
    # And each starter name appears exactly once (no near-duplicates from
    # casing / punctuation differences).
    name_counts: dict[str, int] = {}
    for s in rows:
        name_counts[s.supplier_name] = name_counts.get(s.supplier_name, 0) + 1
    assert all(count == 1 for count in name_counts.values()), name_counts


@pytest.mark.asyncio
async def test_seed_suppliers_normalised_keys_match_starter_list(db_session):
    """Every persisted starter supplier has ``supplier_normalized`` set
    to ``normalize_alias(supplier_name)`` — the column the parser looks
    up against.
    """
    await seed_suppliers(db_session)
    rows = (await db_session.execute(select(Supplier))).scalars().all()
    for s in rows:
        assert s.supplier_normalized == normalize_alias(s.supplier_name)


# ---------------------------------------------------------------------------
# Non-destructive: pre-existing rows are not overwritten
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_seed_suppliers_preserves_existing_row_fields(db_session):
    """A pre-existing row whose normalised key matches a starter name is
    NEVER modified — including ``is_active=False`` and any custom
    ``supplier_name`` casing/spelling the admin chose.

    Concrete scenario: admin imported "BUNNINGS WAREHOUSE PTY LTD" with
    is_active=False (deactivated). Re-running the seed must not flip
    is_active back to True or rename the row to "Bunnings".
    """
    # Pre-seed a custom row whose normalised key collides with "Bunnings".
    # normalize_alias("Bunnings Warehouse Pty Ltd") = "bunningswarehouseptyltd"
    # That does NOT collide. Use a casing variant that DOES collide
    # ("BUNNINGS" → "bunnings") to actually exercise the protection.
    pre = Supplier(supplier_name="BUNNINGS", is_active=False)
    db_session.add(pre)
    await db_session.flush()
    pre_id = pre.supplier_id
    pre_name = pre.supplier_name
    pre_active = pre.is_active

    # Run the seed.
    await seed_suppliers(db_session)

    # The pre-existing row must be unchanged: same id, same name casing,
    # still inactive.
    row = await db_session.get(Supplier, pre_id)
    assert row is not None
    assert row.supplier_name == pre_name, "seed must not rename existing supplier"
    assert row.is_active == pre_active, "seed must not flip is_active on existing supplier"

    # And no second "Bunnings" row exists — the normalised key was already
    # present, so the starter "Bunnings" was skipped.
    rows = (
        await db_session.execute(
            select(Supplier).where(
                Supplier.supplier_normalized == normalize_alias("Bunnings")
            )
        )
    ).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_seed_suppliers_leaves_unrelated_existing_rows_alone(db_session):
    """Suppliers whose names are NOT in the starter list are not touched.

    Pre-seed a non-starter supplier ("ACME Hardware"), run the seed,
    confirm ACME still exists exactly as inserted.
    """
    custom = Supplier(supplier_name="ACME Hardware", is_active=True)
    db_session.add(custom)
    await db_session.flush()
    custom_id = custom.supplier_id

    await seed_suppliers(db_session)

    row = await db_session.get(Supplier, custom_id)
    assert row is not None
    assert row.supplier_name == "ACME Hardware"
    assert row.is_active is True

    # And the starter set is still seeded alongside.
    starter_rows = (
        await db_session.execute(
            select(Supplier).where(Supplier.supplier_name.in_(STARTER_SUPPLIERS))
        )
    ).scalars().all()
    assert len(starter_rows) == len(STARTER_SUPPLIERS)


# ---------------------------------------------------------------------------
# Parser integration — seeded supplier resolves through the natural-
# language path without an explicit alias row.
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def seeded_supplier_world(db_session):
    """Seed the starter supplier set so parser tests have something to match."""
    return await seed_suppliers(db_session)


@pytest.mark.asyncio
async def test_seed_suppliers_parser_resolves_bunnings(db_session, seeded_supplier_world):
    """The parser must resolve a seeded supplier (Bunnings) through the
    real ``raw_input_text`` token-matching path. No alias row exists —
    the match comes from the supplier's normalised name itself, which
    is what the seed populates via the model's ``before_insert``
    listener.
    """
    tokens = tokenize("Bunnings $440 cement")
    result = await match_supplier(tokens, db_session)
    assert result.supplier_id is not None
    # Pull the actual row to confirm the matched id is "Bunnings".
    matched = await db_session.get(Supplier, result.supplier_id)
    assert matched is not None
    assert matched.supplier_name == "Bunnings"


@pytest.mark.asyncio
async def test_seed_suppliers_parser_resolves_lowercase_and_punctuation(
    db_session, seeded_supplier_world
):
    """``"BUNNINGS!"`` and ``"bunnings."`` both resolve via the supplier-name
    normalisation pipeline (NFKC + casefold + punctuation strip).
    """
    for raw in ("BUNNINGS!", "bunnings."):
        tokens = tokenize(raw)
        result = await match_supplier(tokens, db_session)
        assert result.supplier_id is not None, f"failed to match {raw!r}"
        matched = await db_session.get(Supplier, result.supplier_id)
        assert matched is not None and matched.supplier_name == "Bunnings"


@pytest.mark.asyncio
async def test_seed_suppliers_unknown_token_does_not_resolve(
    db_session, seeded_supplier_world
):
    """A token that isn't a seeded supplier (or alias) still returns no
    match — the seed set doesn't accidentally over-match neutral words.
    """
    tokens = tokenize("RandomSupplierNameThatDoesNotExist")
    result = await match_supplier(tokens, db_session)
    assert result.supplier_id is None


# ---------------------------------------------------------------------------
# Starter-list integrity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_starter_list_has_no_duplicate_normalised_keys():
    """The starter list itself must not contain entries whose normalised
    keys collide — that would mean the seed function would skip rows
    based on which name appears first in the tuple.
    """
    normalised = [normalize_alias(name) for name in STARTER_SUPPLIERS]
    assert len(set(normalised)) == len(STARTER_SUPPLIERS), (
        f"STARTER_SUPPLIERS has duplicate normalised keys: {normalised}"
    )
