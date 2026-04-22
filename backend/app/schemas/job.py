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
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.models.job import JobStatus
from app.models.user import LanguageCode
from app.schemas.category import CategoryPublic


class JobCreate(BaseModel):
    """Body of ``POST /jobs`` (admin-only)."""

    job_name: str = Field(min_length=1, max_length=255)
    job_code: str | None = Field(default=None, min_length=1, max_length=64)
    site_address: str | None = Field(default=None, max_length=512)
    contract_value_ex_gst: Decimal | None = Field(default=None, ge=0)
    total_budget_ex_gst: Decimal | None = Field(default=None, ge=0)
    status: JobStatus = JobStatus.active


class JobUpdate(BaseModel):
    """Body of ``PATCH /jobs/{job_id}`` (admin-only)."""

    job_name: str | None = Field(default=None, min_length=1, max_length=255)
    job_code: str | None = Field(default=None, min_length=1, max_length=64)
    site_address: str | None = Field(default=None, max_length=512)
    contract_value_ex_gst: Decimal | None = Field(default=None, ge=0)
    total_budget_ex_gst: Decimal | None = Field(default=None, ge=0)
    status: JobStatus | None = None


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
    """

    model_config = ConfigDict(from_attributes=True)

    job_id: uuid.UUID
    job_code: str | None
    job_name: str
    site_address: str | None
    contract_value_ex_gst: Decimal | None
    total_budget_ex_gst: Decimal | None
    status: JobStatus
    created_by: uuid.UUID


class JobWithDetailPublic(JobPublic):
    """Full serialised job including aliases + category budgets.

    Returned by ``GET /jobs/{job_id}`` only — list responses stay
    compact via ``JobPublic``.
    """

    aliases: list[JobAliasPublic] = []
    category_budgets: list[JobCategoryBudgetPublic] = []
