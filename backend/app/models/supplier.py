"""Supplier + alias models for Phase 2.

A :class:`Supplier` is a merchant / vendor (Bunnings, Reece, a local
timber yard) against which receipt-style expenses are booked. The V1
expense parser (Phase 2) sees natural-language supplier mentions in
both English and Chinese — ``"Bunnings"``, ``"邦宁"``, ``"BWC"``, etc. —
and must resolve each of those back to a single canonical supplier.

:class:`SupplierAlias` rows are free-form strings captured at the time
an admin configures the supplier. They share the same normalisation /
uniqueness discipline as :class:`app.models.job.JobAlias`:

* ``alias_text`` stores exactly what the admin typed.
* ``alias_text_normalized`` is the key produced by
  :func:`app.core.text.normalize_alias` and is globally unique so
  ``"Bunnings"`` cannot simultaneously resolve to two different
  suppliers — that would make parser decisions ambiguous.

``supplier_normalized`` on :class:`Supplier` is the same idea applied
to the canonical supplier name: the parser can fall back to matching
against the supplier name itself when no explicit alias exists.

Both derived columns are kept in sync with their source fields by the
``before_insert`` / ``before_update`` event listeners defined at the
bottom of this module — callers never set the normalised form by hand.
"""

from __future__ import annotations

import uuid

from sqlalchemy import UUID, Boolean
from sqlalchemy import Enum as SqlaEnum
from sqlalchemy import ForeignKey, String, UniqueConstraint, event
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.text import normalize_alias
from app.models.base import Base, TimestampMixin
from app.models.user import LanguageCode


class Supplier(Base, TimestampMixin):
    """A merchant / vendor against which expenses are recorded."""

    __tablename__ = "suppliers"

    supplier_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    supplier_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Derived from ``supplier_name`` via ``normalize_alias``; stored in a
    # column (rather than a functional index) so Alembic can name it
    # stably across upgrades / downgrades. Globally unique so the parser
    # cannot ambiguously match a supplier name.
    supplier_normalized: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )

    aliases: Mapped[list["SupplierAlias"]] = relationship(
        back_populates="supplier",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    __table_args__ = (
        UniqueConstraint(
            "supplier_normalized",
            name="uq_suppliers_supplier_normalized",
        ),
    )


class SupplierAlias(Base, TimestampMixin):
    """A human-facing name under which a :class:`Supplier` can be looked up.

    ``alias_text`` stores exactly what the admin typed; the derived
    ``alias_text_normalized`` is what we index for uniqueness and parser
    matching.
    """

    __tablename__ = "supplier_aliases"

    alias_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    supplier_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("suppliers.supplier_id", ondelete="CASCADE"),
        nullable=False,
    )
    alias_text: Mapped[str] = mapped_column(String(255), nullable=False)
    # Derived from ``alias_text`` via ``normalize_alias``; stored in a
    # column (rather than a functional index) so Alembic can name it
    # stably across upgrades / downgrades.
    alias_text_normalized: Mapped[str] = mapped_column(String(255), nullable=False)
    # ``language_code`` is reused from the users migration — the
    # ``create_type=False`` flag below keeps Alembic from attempting to
    # recreate the Postgres enum type.
    language_code: Mapped[LanguageCode | None] = mapped_column(
        SqlaEnum(
            LanguageCode,
            name="language_code",
            native_enum=True,
            create_type=False,
        ),
        nullable=True,
    )

    supplier: Mapped["Supplier"] = relationship(back_populates="aliases")

    __table_args__ = (
        UniqueConstraint(
            "alias_text_normalized",
            name="uq_supplier_aliases_alias_normalized",
        ),
    )


# Keep ``supplier_normalized`` as a derived invariant of ``supplier_name``
# without forcing every caller (services, tests) to remember. Using
# ``propagate=True`` means subclassing ``Supplier`` (should that ever
# happen) inherits the listener too.
@event.listens_for(Supplier, "before_insert", propagate=True)
@event.listens_for(Supplier, "before_update", propagate=True)
def _sync_supplier_normalized(mapper, connection, target: Supplier) -> None:
    if target.supplier_name is not None:
        target.supplier_normalized = normalize_alias(target.supplier_name)


# Same pattern as ``JobAlias._sync_alias_normalized``: keep the
# normalised alias form in lock-step with ``alias_text``.
@event.listens_for(SupplierAlias, "before_insert", propagate=True)
@event.listens_for(SupplierAlias, "before_update", propagate=True)
def _sync_supplier_alias_normalized(mapper, connection, target: SupplierAlias) -> None:
    if target.alias_text is not None:
        target.alias_text_normalized = normalize_alias(target.alias_text)
