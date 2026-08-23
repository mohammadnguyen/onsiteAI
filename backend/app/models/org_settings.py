"""Singleton organisation-level settings.

One row for the whole tenant (enforced by a unique index on ``(true)``),
seeded by migration ``b7e9f3a2d815`` and get-or-created by the service
for schemas built straight from metadata (tests). Settings here are
PRICING RULES, not per-record facts: ``default_day_hours`` re-prices
hours-less labour entries at read time by design (founder decision
2026-08-24) — contrast ``LabourEntry.rate_snapshot``, which is a
write-once per-entry fact.

No soft delete: the row is configuration, never deleted (same posture
as the Worker roster's "deactivate, never delete").
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import CheckConstraint, Index, Numeric, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class OrgSettings(Base, TimestampMixin):
    """The tenant's single settings row."""

    __tablename__ = "org_settings"

    settings_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # How many hours a "day" of labour is worth when an entry records
    # attendance (day_fraction) without hours. Read-time multiplier:
    # cost = day_fraction * default_day_hours * rate_snapshot. Admin-
    # adjustable; changing it re-prices ALL hours-less entries, past
    # and future (the founder's chosen semantics — days stay days).
    default_day_hours: Mapped[Decimal] = mapped_column(
        Numeric(4, 2), nullable=False, server_default=text("10.00")
    )

    __table_args__ = (
        CheckConstraint(
            "default_day_hours > 0 AND default_day_hours <= 24",
            name="ck_org_settings_default_day_hours",
        ),
        # At most one row, enforced in the DB: every row indexes the
        # same constant expression, so a second insert violates.
        Index("uq_org_settings_singleton", text("(true)"), unique=True),
    )
