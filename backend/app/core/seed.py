"""Seed data for Phase 1 + Capture Hardening Patch (CHP-6) bootstraps.

``seed_builder_categories`` is idempotent (upsert by name): running it twice
leaves the 23-row catalogue unchanged, not duplicated.

``seed_suppliers`` (CHP-6) is idempotent in a stricter sense: it only
INSERTS missing rows and never modifies fields on existing rows. The
intent is a starter set the admin can keep, edit, deactivate, or
extend; the seed must never silently overwrite an admin's edits on
re-run.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.text import normalize_alias
from app.models.category import Category
from app.models.supplier import Supplier

# Order here IS the display_order. Positions 1..23 reflect the spec's
# dashboard ordering; changing the tuple reorders the catalogue on the
# next seed run (seed is idempotent, so existing rows get re-ordered in
# place rather than duplicated).
BUILDER_CATEGORIES: tuple[str, ...] = (
    "Demolition",
    "Earthworks",
    "Concrete",
    "Brickwork",
    "Carpentry",
    "Roofing",
    "Cladding",
    "Waterproofing",
    "Plumbing",
    "Electrical",
    "Gyprock",
    "Painting",
    "Flooring",
    "Tiling",
    "Joinery",
    "Windows & Doors",
    "Structural Steel",
    "Labour",
    "Preliminaries",
    "Equipment Hire",
    "Waste / Skip Bin",
    "Delivery",
    "Miscellaneous",
)


async def seed_builder_categories(db: AsyncSession) -> list[Category]:
    """Insert or update the builder-category catalogue. Idempotent.

    Upsert is keyed on ``category_name``: a matching row gets its
    ``display_order`` and ``is_active`` refreshed; a missing row is
    inserted. Rows with names not in ``BUILDER_CATEGORIES`` are left
    alone (admins may have added them).
    """
    existing = (await db.execute(select(Category))).scalars().all()
    by_name = {c.category_name: c for c in existing}

    result: list[Category] = []
    for idx, name in enumerate(BUILDER_CATEGORIES, start=1):
        cat = by_name.get(name)
        if cat is None:
            cat = Category(category_name=name, display_order=idx, is_active=True)
            db.add(cat)
        else:
            cat.display_order = idx
            cat.is_active = True
        result.append(cat)
    await db.flush()
    return result


# CHP-6: starter supplier list for NSW residential builders.
#
# Match-key is ``normalize_alias(supplier_name)`` (the model's derived
# ``supplier_normalized`` column, kept globally unique by a DB
# constraint). Pre-existing rows with the same normalised key are NEVER
# modified — the seed only INSERTS missing rows. This is intentionally
# stricter than ``seed_builder_categories`` so an admin who has tweaked
# ``Bunnings`` (e.g. deactivated it, or renamed it to "Bunnings
# Warehouse Pty Ltd") doesn't silently lose those edits on a re-run.
#
# The set is deliberately small: ~10 names every NSW residential
# builder bumps into in the first week. The admin can add/extend via
# the existing ``POST /suppliers`` endpoint; they can deactivate any
# they don't use via ``PATCH /suppliers/{id}``.
STARTER_SUPPLIERS: tuple[str, ...] = (
    "Bunnings",
    "Reece",
    "Caesarstone",
    "Stratco",
    "Caroma",
    "Tradelink",
    "Beacon Lighting",
    "Mitre 10",
    "Total Tools",
    "Boral",
    "CSR Gyprock",
)


async def seed_suppliers(db: AsyncSession) -> list[Supplier]:
    """Insert the starter supplier set. Idempotent and non-destructive.

    Lookup is by ``supplier_normalized`` (i.e. ``normalize_alias(name)``)
    so case / punctuation differences in pre-existing rows don't cause
    accidental duplicates.

    Re-run semantics: any starter name whose normalised key matches an
    existing supplier row is **skipped entirely** — no field on the
    existing row is updated. The seed function only INSERTS missing
    rows. This keeps the contract simple: "running the seed never
    overwrites admin edits."

    Returns the full list of ``Supplier`` rows for the starter set,
    whether newly-inserted or pre-existing.
    """
    # Single SELECT — small N, in-memory dict-by-normal lookup beats
    # one-shot-per-name round trips.
    existing = (await db.execute(select(Supplier))).scalars().all()
    by_normal: dict[str, Supplier] = {s.supplier_normalized: s for s in existing}

    result: list[Supplier] = []
    for name in STARTER_SUPPLIERS:
        key = normalize_alias(name)
        existing_row = by_normal.get(key)
        if existing_row is not None:
            # Pre-existing row — DO NOT touch any field. Return as-is so
            # callers can see the canonical row even when it pre-existed.
            result.append(existing_row)
            continue
        new = Supplier(supplier_name=name, is_active=True)
        db.add(new)
        result.append(new)
    await db.flush()
    return result
