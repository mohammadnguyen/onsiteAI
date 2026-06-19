"""Job + alias + category-budget models for Phase 1.

A :class:`Job` is a building site with a contract value and optional
per-category budgets. :class:`JobAlias` rows are free-form strings
(English, Chinese, or mixed) by which the expense parser (Phase 2) will
match natural-language input like ``"工地1"`` or ``"Kelly House"`` back
to a single canonical job.

Aliases are globally unique on their normalised form
(``alias_text_normalized``) so ``"Kelly"`` cannot simultaneously resolve
to two different jobs — that would make parser decisions ambiguous.
The canonical form is produced by :func:`app.core.text.normalize_alias`
and is kept in sync with ``alias_text`` via a ``before_insert`` /
``before_update`` event listener defined at the bottom of this module.
"""

from __future__ import annotations

import enum
import uuid
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import UUID, CheckConstraint
from sqlalchemy import Enum as SqlaEnum
from sqlalchemy import ForeignKey, Numeric, String, UniqueConstraint, event
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.text import normalize_alias
from app.models.base import Base, TimestampMixin
from app.models.user import LanguageCode

if TYPE_CHECKING:
    from app.models.category import Category
    from app.models.user import User


class JobStatus(str, enum.Enum):
    """Lifecycle state of a :class:`Job`.

    ``active`` jobs accept new expenses; ``completed`` jobs are read-only
    in Phase 1 (no separate archival flow is needed for V1).
    """

    active = "active"
    completed = "completed"


class GstMode(str, enum.Enum):
    """How a job's contract amount is entered/displayed for GST.

    ``inclusive`` -> UI "Including GST" (GST job; the entered amount is
    gross, ex-GST revenue = amount / 1.1). ``exclusive`` -> UI "No GST
    (Cash)" (cash / no-GST job; the entered amount IS the revenue,
    GST = 0). The internal term "exclusive" is NEVER shown to users.

    Display-hint only: ``contract_value_ex_gst`` stays the canonical
    ex-GST basis in BOTH modes (inclusive stores entered/1.1; exclusive
    stores the entered amount as-is). The backend runs NO GST math on
    gst_mode — the mobile client converts on entry/display.
    """

    inclusive = "inclusive"
    exclusive = "exclusive"


class Job(Base, TimestampMixin):
    """A building site / contract that aggregates expenses and budgets."""

    __tablename__ = "jobs"

    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # Optional short code; unique when present so admins can search by it.
    job_code: Mapped[str | None] = mapped_column(
        String(64), unique=True, nullable=True
    )
    job_name: Mapped[str] = mapped_column(String(255), nullable=False)
    site_address: Mapped[str | None] = mapped_column(String(512), nullable=True)
    contract_value_ex_gst: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    total_budget_ex_gst: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    # Phase 3 Lite+ — target profit margin as a percent (e.g. 15.00 = 15%).
    # Range constraint enforced at both Pydantic and DB CHECK layers
    # (``ck_jobs_target_profit_ratio_pct_range``). NULL = not set.
    target_profit_ratio_pct: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 2), nullable=True
    )
    # Phase 3 Lite+ — per-job warning thresholds for the budget chip.
    # Both nullable; NULL means "use the system default" — that fallback
    # is resolved at the API boundary (see ``services/budget_summary.
    # _effective_thresholds``) and is intentionally never written back
    # to the column. Stored values stay nullable so the UI can tell
    # "user explicitly set 80" apart from "user left at default 80".
    warning_amber_pct: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 2), nullable=True
    )
    warning_red_pct: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 2), nullable=True
    )
    status: Mapped[JobStatus] = mapped_column(
        SqlaEnum(
            JobStatus, name="job_status", native_enum=True, create_type=True
        ),
        nullable=False,
        default=JobStatus.active,
        server_default=JobStatus.active.value,
    )
    # F2 — per-job contract GST basis (additive). Existing rows default to
    # ``exclusive`` ("No GST (Cash)"), preserving today's behaviour exactly.
    # The backend never branches on this: ``contract_value_ex_gst`` stays
    # the ex-GST basis; gst_mode is a mobile display/interpretation hint.
    gst_mode: Mapped[GstMode] = mapped_column(
        SqlaEnum(GstMode, name="gst_mode", native_enum=True, create_type=True),
        nullable=False,
        default=GstMode.exclusive,
        server_default=GstMode.exclusive.value,
    )
    # The admin who created the job. V1 does not delete users, so a plain
    # NOT NULL FK with no ondelete is sufficient — see Task 7 plan.
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=False
    )

    aliases: Mapped[list["JobAlias"]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    category_budgets: Mapped[list["JobCategoryBudget"]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    created_by_user: Mapped["User"] = relationship(lazy="joined")

    # CHECK constraints mirror the Alembic migration
    # ``b3e7a8f1c042_add_job_target_profit_and_thresholds`` so the test
    # bootstrap (which uses ``Base.metadata.create_all`` rather than
    # Alembic) builds an identical schema and the constraint tests
    # exercise the real DB-level enforcement, not just Pydantic.
    __table_args__ = (
        CheckConstraint(
            "target_profit_ratio_pct IS NULL OR "
            "(target_profit_ratio_pct >= 0 AND target_profit_ratio_pct < 100)",
            name="ck_jobs_target_profit_ratio_pct_range",
        ),
        CheckConstraint(
            "warning_amber_pct IS NULL OR warning_amber_pct >= 0",
            name="ck_jobs_warning_amber_pct_nonneg",
        ),
        CheckConstraint(
            "warning_red_pct IS NULL OR warning_red_pct > 0",
            name="ck_jobs_warning_red_pct_positive",
        ),
        CheckConstraint(
            "warning_amber_pct IS NULL OR warning_red_pct IS NULL OR "
            "warning_amber_pct < warning_red_pct",
            name="ck_jobs_warning_amber_lt_red",
        ),
    )


class JobAlias(Base, TimestampMixin):
    """A human-facing name under which a :class:`Job` can be looked up.

    ``alias_text`` stores exactly what the admin typed; the derived
    ``alias_text_normalized`` is what we index for uniqueness and parser
    matching.
    """

    __tablename__ = "job_aliases"

    alias_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jobs.job_id", ondelete="CASCADE"),
        nullable=False,
    )
    alias_text: Mapped[str] = mapped_column(String(255), nullable=False)
    # Derived from ``alias_text`` via ``normalize_alias``; stored in a
    # column (rather than a functional index) so Alembic can name it
    # stably across upgrades / downgrades.
    alias_text_normalized: Mapped[str] = mapped_column(
        String(255), nullable=False
    )
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

    job: Mapped["Job"] = relationship(back_populates="aliases")

    __table_args__ = (
        UniqueConstraint(
            "alias_text_normalized",
            name="uq_job_aliases_alias_normalized",
        ),
    )


class JobCategoryBudget(Base, TimestampMixin):
    """Per-category budget allocation on a :class:`Job`.

    ``(job_id, category_id)`` is unique: a job can have at most one
    budget row per category.
    """

    __tablename__ = "job_category_budgets"

    budget_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jobs.job_id", ondelete="CASCADE"),
        nullable=False,
    )
    category_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("categories.category_id"),
        nullable=False,
    )
    budget_amount_ex_gst: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False
    )

    job: Mapped["Job"] = relationship(back_populates="category_budgets")
    category: Mapped["Category"] = relationship(lazy="joined")

    __table_args__ = (
        UniqueConstraint(
            "job_id",
            "category_id",
            name="uq_job_category_budgets_job_category",
        ),
    )


# Keep ``alias_text_normalized`` as a derived invariant of ``alias_text``
# without forcing every caller (services, tests) to remember. Using
# ``propagate=True`` means subclassing ``JobAlias`` (should that ever
# happen) inherits the listener too.
@event.listens_for(JobAlias, "before_insert", propagate=True)
@event.listens_for(JobAlias, "before_update", propagate=True)
def _sync_alias_normalized(mapper, connection, target: JobAlias) -> None:
    if target.alias_text is not None:
        target.alias_text_normalized = normalize_alias(target.alias_text)
