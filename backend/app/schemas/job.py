"""Public-facing job / alias / category-budget schemas.

``JobPublic`` is the compact wire shape used by ``GET /jobs`` (list) and
by create/update/patch responses. ``JobWithDetailPublic`` extends it with
eager-loaded aliases + category budgets for ``GET /jobs/{id}``. Separate
``JobAliasPublic`` / ``JobCategoryBudgetPublic`` rows are also returned
standalone from the POST nested-resource endpoints.

``JobCreate`` / ``JobUpdate`` / ``JobAliasCreate`` /
``JobCategoryBudgetCreate`` are the inbound body shapes. Everything on
``JobUpdate`` is optional so partial updates don't force callers to
round-trip values they don't want to touch (same convention as Task 6's
``CategoryUpdate``).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.job import GstMode, JobStatus
from app.models.user import LanguageCode
from app.schemas.budget_summary import JobSummary
from app.schemas.category import CategoryPublic


def _validate_amber_lt_red(
    amber: Decimal | None, red: Decimal | None
) -> None:
    """Cross-field check: amber must be strictly less than red when both set.

    Mirrors the DB CHECK ``ck_jobs_warning_amber_lt_red`` so callers see
    a 422 from Pydantic before reaching the DB. NULL-safe.
    """
    if amber is not None and red is not None and amber >= red:
        raise ValueError(
            "warning_amber_pct must be strictly less than warning_red_pct"
        )


class JobCreate(BaseModel):
    """Body of ``POST /jobs`` (admin-only).

    Phase 3 Lite+ adds three optional percent fields. All three are
    enforced by both Pydantic (here) and DB CHECK constraints (see
    ``backend/app/models/job.py`` and migration ``b3e7a8f1c042``).
    """

    job_name: str = Field(min_length=1, max_length=255)
    job_code: str | None = Field(default=None, min_length=1, max_length=64)
    site_address: str | None = Field(default=None, max_length=512)
    contract_value_ex_gst: Decimal | None = Field(default=None, ge=0)
    total_budget_ex_gst: Decimal | None = Field(default=None, ge=0)
    target_profit_ratio_pct: Decimal | None = Field(
        default=None, ge=0, lt=100
    )
    warning_amber_pct: Decimal | None = Field(default=None, ge=0)
    warning_red_pct: Decimal | None = Field(default=None, gt=0)
    status: JobStatus = JobStatus.active
    gst_mode: GstMode = GstMode.exclusive

    @model_validator(mode="after")
    def _amber_lt_red(self) -> "JobCreate":
        _validate_amber_lt_red(self.warning_amber_pct, self.warning_red_pct)
        return self


class JobUpdate(BaseModel):
    """Body of ``PATCH /jobs/{job_id}`` (admin-only).

    Phase 3 Lite+ extends with the same three optional percent fields
    as ``JobCreate``. The cross-field amber-lt-red check still applies
    on PATCH; if only one of (amber, red) is supplied, it must still
    be coherent with whatever the row already holds — that combined
    check is enforced server-side by the DB CHECK constraint after the
    update is applied (Pydantic only sees the patch payload).
    """

    job_name: str | None = Field(default=None, min_length=1, max_length=255)
    job_code: str | None = Field(default=None, min_length=1, max_length=64)
    site_address: str | None = Field(default=None, max_length=512)
    contract_value_ex_gst: Decimal | None = Field(default=None, ge=0)
    total_budget_ex_gst: Decimal | None = Field(default=None, ge=0)
    target_profit_ratio_pct: Decimal | None = Field(
        default=None, ge=0, lt=100
    )
    warning_amber_pct: Decimal | None = Field(default=None, ge=0)
    warning_red_pct: Decimal | None = Field(default=None, gt=0)
    status: JobStatus | None = None
    gst_mode: GstMode | None = None

    @model_validator(mode="after")
    def _amber_lt_red(self) -> "JobUpdate":
        _validate_amber_lt_red(self.warning_amber_pct, self.warning_red_pct)
        return self


class JobAliasCreate(BaseModel):
    """Body of ``POST /jobs/{job_id}/aliases`` (admin-only)."""

    alias_text: str = Field(min_length=1, max_length=255)
    language_code: LanguageCode | None = None


class JobAliasPublic(BaseModel):
    """Serialised view of a :class:`~app.models.job.JobAlias`."""

    model_config = ConfigDict(from_attributes=True)

    alias_id: uuid.UUID
    job_id: uuid.UUID
    alias_text: str
    alias_text_normalized: str
    language_code: LanguageCode | None


class JobCategoryBudgetCreate(BaseModel):
    """Body of ``POST /jobs/{job_id}/category-budgets`` (admin-only)."""

    category_id: uuid.UUID
    budget_amount_ex_gst: Decimal = Field(ge=0)


class JobCategoryBudgetUpdate(BaseModel):
    """Body of ``PATCH /jobs/{job_id}/category-budgets/{budget_id}`` (admin-only).

    Single editable field — ``budget_amount_ex_gst``. The underlying DB
    column is NOT NULL, so this field is required (not Optional). 0 is
    a valid value (the operator explicitly approved zero budgets);
    negative values are rejected at the schema layer via ``ge=0``,
    surfacing as 422 from FastAPI before reaching the service.

    No partial-update semantics needed because there's only one
    editable field — if more fields ever become editable, the conditional-
    spread pattern from ``JobUpdate`` is the model to copy.
    """

    budget_amount_ex_gst: Decimal = Field(ge=0)


class JobCategoryBudgetPublic(BaseModel):
    """Serialised view of a :class:`~app.models.job.JobCategoryBudget`.

    The nested ``category`` is eager-loaded via the ``lazy="joined"``
    relationship on the model, so the HTTP response always carries the
    category's public shape inline — no extra round trip.
    """

    model_config = ConfigDict(from_attributes=True)

    budget_id: uuid.UUID
    job_id: uuid.UUID
    category_id: uuid.UUID
    budget_amount_ex_gst: Decimal
    category: CategoryPublic


class JobPublic(BaseModel):
    """Compact serialised view of a :class:`~app.models.job.Job`.

    Used by ``GET /jobs`` (list) and by non-detail POST/PATCH responses.

    The optional ``summary`` field is populated by ``GET /jobs`` (Phase 3
    Lite) so the list page can render budget visibility per row without a
    second round trip. Create/update responses leave it ``None``: those
    are write-action results, not snapshots, and the cost of a one-off
    aggregation on every write is not justified by the dashboard need.
    """

    model_config = ConfigDict(from_attributes=True)

    job_id: uuid.UUID
    job_code: str | None
    job_name: str
    site_address: str | None
    contract_value_ex_gst: Decimal | None
    total_budget_ex_gst: Decimal | None
    # Phase 3 Lite+ stored fields. May be NULL — the API never overwrites
    # NULL with a default. Effective values (with the 80 / 100 fallback)
    # are surfaced separately on the embedded ``summary``.
    target_profit_ratio_pct: Decimal | None = None
    warning_amber_pct: Decimal | None = None
    warning_red_pct: Decimal | None = None
    status: JobStatus
    gst_mode: GstMode = GstMode.exclusive
    created_by: uuid.UUID
    summary: JobSummary | None = None


class JobWithDetailPublic(JobPublic):
    """Full serialised job including aliases + category budgets.

    Returned by ``GET /jobs/{job_id}`` only — list responses stay
    compact via ``JobPublic``.
    """

    aliases: list[JobAliasPublic] = []
    category_budgets: list[JobCategoryBudgetPublic] = []


class JobAuditRow(BaseModel):
    """Serialised view of a :class:`~app.models.job_audit_log.JobAuditLog` row.

    Returned by ``GET /jobs/{job_id}/audit`` (admin only). Pre-edit
    snapshots (``job_name_snapshot``, ``job_code_snapshot``) keep the
    row readable after the parent job is gone (v1A-3 hard delete).
    """

    model_config = ConfigDict(from_attributes=True)

    audit_id: uuid.UUID
    tenant_id: uuid.UUID
    job_id: uuid.UUID | None
    job_name_snapshot: str
    job_code_snapshot: str | None
    actor_user_id: uuid.UUID
    action: str
    changed_fields: dict
    created_at: datetime
