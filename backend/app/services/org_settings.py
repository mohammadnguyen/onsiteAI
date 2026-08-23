"""Org-settings service: read and update the singleton row.

Get-or-create keeps the service correct on BOTH schema paths: live DBs
have the row seeded by migration ``b7e9f3a2d815``; test DBs built from
metadata start empty and create it on first read. The insert uses
ON CONFLICT DO NOTHING against the singleton unique index, so a
concurrent first-read cannot raise — both callers converge on the one
row.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.org_settings import OrgSettings


async def get_org_settings(db: AsyncSession) -> OrgSettings:
    """Return the singleton settings row, creating it if absent."""
    row = (await db.execute(select(OrgSettings))).scalar_one_or_none()
    if row is not None:
        return row
    await db.execute(
        pg_insert(OrgSettings).values().on_conflict_do_nothing()
    )
    return (await db.execute(select(OrgSettings))).scalar_one()


async def get_default_day_hours(db: AsyncSession) -> Decimal:
    """The hours a "day" is worth when a labour entry has no hours."""
    return (await get_org_settings(db)).default_day_hours


async def update_org_settings(
    db: AsyncSession, *, default_day_hours: Decimal
) -> OrgSettings:
    """Admin update. Bounds are validated by the schema and backstopped
    by the DB CHECK; the change is deliberately retroactive for cost
    derivation (pricing rule, not a per-entry fact)."""
    row = await get_org_settings(db)
    row.default_day_hours = default_day_hours
    await db.flush()
    await db.refresh(row)
    return row
