"""Category catalogue — the 23 builder categories seeded by ``seed_builder_categories``.

Plan Phase 1 does not treat categories as an editable resource; admins can add
or rename via the API but the seed list is the baseline. Phase 2 expenses
attach to a category via FK.
"""

from __future__ import annotations

import uuid

from sqlalchemy import UUID, Boolean, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class Category(Base, TimestampMixin):
    """A builder-category row (e.g. ``Concrete``, ``Plumbing``)."""

    __tablename__ = "categories"

    category_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    category_name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    display_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
