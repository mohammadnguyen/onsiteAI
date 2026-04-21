"""Seed data for Phase 1 bootstraps.

``seed_builder_categories`` is idempotent (upsert by name): running it twice
leaves the 23-row catalogue unchanged, not duplicated.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.category import Category

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
