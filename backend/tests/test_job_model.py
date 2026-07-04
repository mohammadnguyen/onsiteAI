"""Task 7: model-level tests for ``Job`` / ``JobAlias`` / ``JobCategoryBudget``.

These exercise the things a Phase 2 service layer will rely on:

* relationship loading (a job carries its aliases + category budgets)
* the ``before_insert`` listener that populates ``alias_text_normalized``
* the global UNIQUE on normalised aliases — two jobs cannot both claim
  the same normalised form (parser ambiguity prevention)
* the composite UNIQUE on ``(job_id, category_id)`` — one budget row per
  category per job
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import (
    Job,
    JobAlias,
    JobCategoryBudget,
    JobStatus,
    LanguageCode,
)


async def _make_job(db_session, admin, *, name: str, code: str | None = None) -> Job:
    job = Job(
        job_id=uuid.uuid4(),
        job_code=code,
        job_name=name,
        status=JobStatus.active,
        created_by=admin.user_id,
    )
    db_session.add(job)
    await db_session.flush()
    return job


@pytest.mark.asyncio
async def test_job_with_aliases_and_budgets_roundtrips(
    db_session, seeded_admin, seed_categories
):
    """A Job persists with two aliases + two budgets and reloads via relationships."""
    job = await _make_job(db_session, seeded_admin, name="Kelly House")

    db_session.add_all(
        [
            JobAlias(job_id=job.job_id, alias_text="Kelly", language_code=LanguageCode.en),
            JobAlias(job_id=job.job_id, alias_text="工地1", language_code=LanguageCode.zh),
        ]
    )
    cat_plumbing = seed_categories[8]  # "Plumbing"
    cat_electrical = seed_categories[9]  # "Electrical"
    db_session.add_all(
        [
            JobCategoryBudget(
                job_id=job.job_id,
                category_id=cat_plumbing.category_id,
                budget_amount_ex_gst=Decimal("25000.00"),
            ),
            JobCategoryBudget(
                job_id=job.job_id,
                category_id=cat_electrical.category_id,
                budget_amount_ex_gst=Decimal("18000.00"),
            ),
        ]
    )
    await db_session.flush()
    await db_session.refresh(job, ["aliases", "category_budgets"])

    assert job.job_name == "Kelly House"
    assert job.status == JobStatus.active
    assert job.created_by == seeded_admin.user_id
    assert len(job.aliases) == 2
    assert {a.alias_text for a in job.aliases} == {"Kelly", "工地1"}
    assert len(job.category_budgets) == 2
    assert sum(b.budget_amount_ex_gst for b in job.category_budgets) == Decimal(
        "43000.00"
    )


@pytest.mark.asyncio
async def test_alias_normalized_is_auto_populated(db_session, seeded_admin):
    """The ``before_insert`` listener sets ``alias_text_normalized`` from ``alias_text``."""
    job = await _make_job(db_session, seeded_admin, name="Kelly House")

    alias = JobAlias(job_id=job.job_id, alias_text="Kelly House")
    db_session.add(alias)
    await db_session.flush()

    assert alias.alias_text_normalized == "kellyhouse"


@pytest.mark.asyncio
async def test_duplicate_normalized_alias_across_jobs_raises(
    db_session, seeded_admin
):
    """Two jobs cannot both claim the same normalised alias."""
    job_a = await _make_job(db_session, seeded_admin, name="Kelly House")
    job_b = await _make_job(db_session, seeded_admin, name="Kelly Apartment")

    db_session.add(JobAlias(job_id=job_a.job_id, alias_text="Kelly"))
    await db_session.flush()

    # Use a SAVEPOINT so the expected IntegrityError only poisons the
    # inner block, not the outer rollback-on-teardown transaction.
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            db_session.add(JobAlias(job_id=job_b.job_id, alias_text="KELLY"))
            await db_session.flush()


@pytest.mark.asyncio
async def test_duplicate_job_category_budget_raises(
    db_session, seeded_admin, seed_categories
):
    """A job can have at most one budget row per category."""
    job = await _make_job(db_session, seeded_admin, name="Kelly House")
    cat = seed_categories[8]  # "Plumbing"

    db_session.add(
        JobCategoryBudget(
            job_id=job.job_id,
            category_id=cat.category_id,
            budget_amount_ex_gst=Decimal("25000.00"),
        )
    )
    await db_session.flush()

    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            db_session.add(
                JobCategoryBudget(
                    job_id=job.job_id,
                    category_id=cat.category_id,
                    budget_amount_ex_gst=Decimal("30000.00"),
                )
            )
            await db_session.flush()
