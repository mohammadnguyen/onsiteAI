"""Phase 2 Task T-K: DB-backed end-to-end tests for the parser pipeline.

Exercises :func:`app.services.parser.parse` against real Postgres (5433)
with the stitched-together stage graph — tokenizer + amount + job +
supplier + category + payment + description + LLM seam + duplicate
detection + review-reason derivation.

The ``seeded_pipeline_world`` fixture builds a small but realistic
world (two active jobs with EN + zh aliases, two active suppliers,
plus the 23 builder categories via :func:`seed_categories`) so each
test can focus on a single orchestration concern.

Coverage groups:

* spec anchors — the two canonical plan examples (``"$305 Bunnings
  Kelly bluemetal"`` clean, ``"工地1 水工材料 163"`` review) plus the
  unsupported-currency case (``"¥50 Kelly"``)
* the LLM seam — called when review fires, skipped otherwise, and
  honoured when it returns a modified partial
* duplicate detection — positive and negative paths
* the mutation contract — :class:`ParseResult` is frozen, and the
  orchestrator produces new partials via :func:`dataclasses.replace`
  rather than in-place mutation
* ambiguity diagnostics — ``ambiguous_job_matches`` tuple preserved
  through the orchestrator to the :class:`ParseResult`
* ``source_per_field`` provenance after a clean rules parse
"""

from __future__ import annotations

import dataclasses
import uuid
from dataclasses import FrozenInstanceError
from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models import (
    Category,
    Expense,
    ExpenseType,
    Job,
    JobAlias,
    JobStatus,
    LanguageCode,
    PaymentMethod,
    ReviewReasonCode,
    ReviewStatus,
    Supplier,
    SupplierAlias,
)
from app.models.expense import ReviewStatus as ExpenseReviewStatus
from app.services.parser import (
    LLMParser,
    MockLLMParser,
    ParsePartial,
    parse,
)

# ---------------------------------------------------------------------------
# Helpers + the shared world fixture
# ---------------------------------------------------------------------------


async def _make_job(
    db_session,
    admin,
    *,
    name: str,
    code: str,
) -> Job:
    """Insert an active :class:`Job` into the current transaction."""
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


async def _make_supplier(db_session, *, name: str) -> Supplier:
    """Insert an active :class:`Supplier` into the current transaction."""
    supplier = Supplier(
        supplier_id=uuid.uuid4(),
        supplier_name=name,
        is_active=True,
    )
    db_session.add(supplier)
    await db_session.flush()
    return supplier


@pytest_asyncio.fixture
async def seeded_pipeline_world(db_session, seeded_admin, seed_categories):
    """Seed the combined job + supplier + category world for pipeline tests.

    * Job A — ``Kelly House`` (code ``KH-01``), aliases ``Kelly``,
      ``工地1``, status=active.
    * Job B — ``Smith Reno`` (code ``SR-02``), alias ``Smith``,
      status=active.
    * Supplier A — ``Bunnings`` (no aliases — the name route wins).
    * Supplier B — ``Mitre 10`` with alias ``Mitre``.
    * 23 builder categories seeded by the ``seed_categories`` fixture.

    Returned as a dict keyed by friendly names so tests can index by
    name instead of tuple position.
    """
    job_a = await _make_job(db_session, seeded_admin, name="Kelly House", code="KH-01")
    db_session.add_all(
        [
            JobAlias(
                job_id=job_a.job_id,
                alias_text="Kelly",
                language_code=LanguageCode.en,
            ),
            JobAlias(
                job_id=job_a.job_id,
                alias_text="工地1",
                language_code=LanguageCode.zh,
            ),
        ]
    )

    job_b = await _make_job(db_session, seeded_admin, name="Smith Reno", code="SR-02")
    db_session.add(
        JobAlias(
            job_id=job_b.job_id,
            alias_text="Smith",
            language_code=LanguageCode.en,
        )
    )

    sup_a = await _make_supplier(db_session, name="Bunnings")

    sup_b = await _make_supplier(db_session, name="Mitre 10")
    db_session.add(
        SupplierAlias(
            supplier_id=sup_b.supplier_id,
            alias_text="Mitre",
            language_code=LanguageCode.en,
        )
    )

    await db_session.flush()

    # Look up a couple of categories by name — tests pin confidence +
    # id, so we need the actual UUIDs from the seed.
    cat_rows = (await db_session.execute(select(Category))).scalars().all()
    by_name = {c.category_name: c for c in cat_rows}

    return {
        "admin": seeded_admin,
        "job_a": job_a,
        "job_b": job_b,
        "sup_a": sup_a,
        "sup_b": sup_b,
        "earthworks": by_name["Earthworks"],
        "plumbing": by_name["Plumbing"],
    }


class SpyLLMParser(LLMParser):
    """Test double that records calls and optionally rewrites the partial.

    Used by the LLM-seam tests:

    * ``calls`` — list of ``(raw_text, rules_partial)`` pairs the spy
      saw. Assertions check length for "was called?" and contents for
      "what was it told?".
    * ``returns`` — optional callable ``(partial) -> partial``. When
      supplied, the spy pipes the rules partial through it and returns
      the result (letting a test override ``amount_value`` or similar
      via :func:`dataclasses.replace`). When ``None`` the spy behaves
      like :class:`MockLLMParser` — returns the input unchanged.
    """

    def __init__(self, returns=None):
        self.calls: list[tuple[str, ParsePartial]] = []
        self.returns = returns

    async def parse(self, raw_text: str, rules_partial: ParsePartial) -> ParsePartial:
        self.calls.append((raw_text, rules_partial))
        if self.returns is not None:
            return self.returns(rules_partial)
        return rules_partial


# ---------------------------------------------------------------------------
# Spec anchors — the three canonical examples from the Phase 2 plan
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spec_anchor_one_reviewed(db_session, seeded_pipeline_world):
    """``"$305 Bunnings Kelly bluemetal"`` — clean parse, no review."""
    w = seeded_pipeline_world
    today = date(2026, 4, 21)

    result = await parse(
        raw_text="$305 Bunnings Kelly bluemetal",
        db=db_session,
        entered_by=w["admin"],
        expense_date=today,
        expense_type=ExpenseType.supplier_expense,
    )

    p = result.partial
    assert p.amount_value == Decimal("305")
    assert p.amount_conf == 0.9
    assert p.unsupported_currency is False
    assert p.job_id == w["job_a"].job_id
    assert p.job_conf == 0.95
    assert p.supplier_id == w["sup_a"].supplier_id
    assert p.supplier_conf == 0.95
    assert p.category_id == w["earthworks"].category_id
    # Single-keyword hit (``bluemetal``) → 0.85.
    assert p.category_conf == 0.85
    assert p.payment_method == PaymentMethod.unknown
    assert p.duplicate_flag is False
    assert p.duplicate_of_expense_id is None

    assert result.review_status == ReviewStatus.reviewed
    assert result.review_reasons == ()

    # Provenance: every rules-populated field is tagged ``"rules"``.
    for field in (
        "amount",
        "job",
        "supplier",
        "category",
        "payment",
        "description",
        "expense_type",
    ):
        assert p.source_per_field[field] == "rules"


@pytest.mark.asyncio
async def test_spec_anchor_two_pending(db_session, seeded_pipeline_world):
    """``"工地1 水工材料 163"`` — zh alias match, low confidences → review."""
    w = seeded_pipeline_world
    today = date(2026, 4, 21)

    result = await parse(
        raw_text="工地1 水工材料 163",
        db=db_session,
        entered_by=w["admin"],
        expense_date=today,
        expense_type=ExpenseType.supplier_expense,
    )

    p = result.partial
    # Bare integer → medium confidence.
    assert p.amount_value == Decimal("163")
    assert p.amount_conf == 0.5
    assert p.unsupported_currency is False

    # Job via zh alias.
    assert p.job_id == w["job_a"].job_id
    assert p.job_conf == 0.95

    # Supplier: two non-ASCII word tokens are both candidate-like
    # (工地1 + 水工材料), so the candidate proposal collapses to
    # confidence 0.0 with no candidate name.
    assert p.supplier_id is None
    assert p.supplier_conf == 0.0
    assert p.candidate_supplier_name is None

    # Category via 水工材料 (Plumbing keyword) → 0.85.
    assert p.category_id == w["plumbing"].category_id
    assert p.category_conf == 0.85

    assert result.review_status == ReviewStatus.pending
    # At minimum, amount_uncertain + supplier_uncertain fire.
    assert ReviewReasonCode.amount_uncertain in result.review_reasons
    assert ReviewReasonCode.supplier_uncertain in result.review_reasons


@pytest.mark.asyncio
async def test_spec_anchor_three_unsupported_currency(
    db_session,
    seeded_pipeline_world,
):
    """``"¥50 Kelly"`` — unsupported currency → flagged + amount uncertain."""
    w = seeded_pipeline_world
    today = date(2026, 4, 21)

    result = await parse(
        raw_text="¥50 Kelly",
        db=db_session,
        entered_by=w["admin"],
        expense_date=today,
        expense_type=ExpenseType.supplier_expense,
    )

    p = result.partial
    assert p.amount_value == Decimal("50")
    assert p.amount_conf == 0.3
    assert p.unsupported_currency is True
    assert p.job_id == w["job_a"].job_id
    assert p.job_conf == 0.95

    assert result.review_status == ReviewStatus.pending
    # Unsupported currency AND amount uncertain (conf 0.3 < 0.8).
    assert ReviewReasonCode.unsupported_currency in result.review_reasons
    assert ReviewReasonCode.amount_uncertain in result.review_reasons


# ---------------------------------------------------------------------------
# LLM seam — gated on review reasons + honours returned partial
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mock_llm_called_when_review_reasons_fire(
    db_session,
    seeded_pipeline_world,
):
    """A review-triggering input hits the configured LLMParser once."""
    w = seeded_pipeline_world
    spy = SpyLLMParser()

    result = await parse(
        raw_text="工地1 水工材料 163",
        db=db_session,
        entered_by=w["admin"],
        expense_date=date(2026, 4, 21),
        expense_type=ExpenseType.supplier_expense,
        llm_parser=spy,
    )

    assert len(spy.calls) == 1
    called_raw, called_partial = spy.calls[0]
    assert called_raw == "工地1 水工材料 163"
    # The spy is handed the rules-derived partial (not mutated).
    assert called_partial.raw_text == "工地1 水工材料 163"
    assert result.review_status == ReviewStatus.pending


@pytest.mark.asyncio
async def test_mock_llm_not_called_when_no_review_reasons(
    db_session,
    seeded_pipeline_world,
):
    """A clean parse skips the LLM entirely."""
    w = seeded_pipeline_world
    spy = SpyLLMParser()

    result = await parse(
        raw_text="$305 Bunnings Kelly bluemetal",
        db=db_session,
        entered_by=w["admin"],
        expense_date=date(2026, 4, 21),
        expense_type=ExpenseType.supplier_expense,
        llm_parser=spy,
    )

    assert spy.calls == []
    assert result.review_status == ReviewStatus.reviewed


@pytest.mark.asyncio
async def test_llm_returning_new_partial_is_honoured(
    db_session,
    seeded_pipeline_world,
):
    """An LLM that replace()s a field has its changes reflected downstream."""
    w = seeded_pipeline_world

    def _override_amount(partial: ParsePartial) -> ParsePartial:
        # Mimic a Phase 2.5 ClaudeLLMParser: produce a new partial via
        # ``dataclasses.replace`` with the overridden field AND a
        # ``source_per_field`` bump to ``"llm"``.
        new_source = {**partial.source_per_field, "amount": "llm"}
        return dataclasses.replace(
            partial,
            amount_value=Decimal("999.99"),
            amount_conf=0.99,
            source_per_field=new_source,
        )

    spy = SpyLLMParser(returns=_override_amount)

    result = await parse(
        raw_text="工地1 水工材料 163",
        db=db_session,
        entered_by=w["admin"],
        expense_date=date(2026, 4, 21),
        expense_type=ExpenseType.supplier_expense,
        llm_parser=spy,
    )

    # Orchestrator accepts the LLM's updated values.
    assert result.partial.amount_value == Decimal("999.99")
    assert result.partial.amount_conf == 0.99
    assert result.partial.source_per_field["amount"] == "llm"
    # The LLM override was high-confidence — amount_uncertain should
    # NOT fire on the post-LLM partial (0.99 ≥ 0.8).
    assert ReviewReasonCode.amount_uncertain not in result.review_reasons


# ---------------------------------------------------------------------------
# Duplicate detection — positive + negative paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_duplicate_detection_fires(db_session, seeded_pipeline_world):
    """A matching prior expense → duplicate_flag + duplicate_suspected reason."""
    w = seeded_pipeline_world
    today = date(2026, 4, 21)

    # Seed a prior expense that matches what ``"$305 Bunnings Kelly bluemetal"``
    # will parse to: job_a, supplier_a (Bunnings), $305, today.
    prior = Expense(
        expense_id=uuid.uuid4(),
        job_id=w["job_a"].job_id,
        supplier_id=w["sup_a"].supplier_id,
        entered_by_user_id=w["admin"].user_id,
        expense_type=ExpenseType.supplier_expense,
        description="Bunnings Kelly bluemetal",
        amount_inc_gst=Decimal("305"),
        expense_date=today,
        review_status=ExpenseReviewStatus.reviewed,
    )
    db_session.add(prior)
    await db_session.flush()

    result = await parse(
        raw_text="$305 Bunnings Kelly bluemetal",
        db=db_session,
        entered_by=w["admin"],
        expense_date=today,
        expense_type=ExpenseType.supplier_expense,
    )

    assert result.partial.duplicate_flag is True
    assert result.partial.duplicate_of_expense_id == prior.expense_id
    assert ReviewReasonCode.duplicate_suspected in result.review_reasons
    # A duplicate hit flips the verdict to pending.
    assert result.review_status == ReviewStatus.pending


@pytest.mark.asyncio
async def test_duplicate_not_fired_when_no_prior_match(
    db_session,
    seeded_pipeline_world,
):
    """No matching prior → duplicate_flag stays False and no reason fires."""
    w = seeded_pipeline_world
    today = date(2026, 4, 21)

    result = await parse(
        raw_text="$305 Bunnings Kelly bluemetal",
        db=db_session,
        entered_by=w["admin"],
        expense_date=today,
        expense_type=ExpenseType.supplier_expense,
    )

    assert result.partial.duplicate_flag is False
    assert result.partial.duplicate_of_expense_id is None
    assert ReviewReasonCode.duplicate_suspected not in result.review_reasons


# ---------------------------------------------------------------------------
# Mutation contract — ParseResult frozen + no in-place mutation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_orchestrator_does_not_mutate_partial_after_duplicate_hit(
    db_session,
    seeded_pipeline_world,
):
    """Duplicate detection updates via replace() — the pre-LLM and post-dup
    partials have different object identities when the dup fires."""
    w = seeded_pipeline_world
    today = date(2026, 4, 21)

    # Seed a prior matching expense so the dup path runs.
    prior = Expense(
        expense_id=uuid.uuid4(),
        job_id=w["job_a"].job_id,
        supplier_id=w["sup_a"].supplier_id,
        entered_by_user_id=w["admin"].user_id,
        expense_type=ExpenseType.supplier_expense,
        description="Bunnings Kelly bluemetal",
        amount_inc_gst=Decimal("305"),
        expense_date=today,
        review_status=ExpenseReviewStatus.reviewed,
    )
    db_session.add(prior)
    await db_session.flush()

    # Use a spy that captures the pre-LLM partial so we can compare
    # identities afterwards. A clean parse wouldn't trigger the LLM,
    # but a duplicate's ``duplicate_suspected`` reason only appears
    # after step 10 — so we force the seam to run by piggy-backing on
    # an input that does have a pre-LLM review reason (bare integer →
    # amount_uncertain).
    captured: list[ParsePartial] = []

    class _Capture(LLMParser):
        async def parse(self, raw_text, rules_partial):
            captured.append(rules_partial)
            return rules_partial

    result = await parse(
        raw_text="Bunnings Kelly bluemetal 305",  # bare integer (no $)
        db=db_session,
        entered_by=w["admin"],
        expense_date=today,
        expense_type=ExpenseType.supplier_expense,
        llm_parser=_Capture(),
    )

    # The spy saw a partial pre-duplicate-check. It should be a
    # different object from the final ``result.partial`` because
    # duplicate detection fired and rebuilt via ``dataclasses.replace``.
    assert len(captured) == 1
    pre_dup = captured[0]
    assert pre_dup.duplicate_flag is False
    assert result.partial.duplicate_flag is True
    assert pre_dup is not result.partial


@pytest.mark.asyncio
async def test_parse_result_is_frozen(db_session, seeded_pipeline_world):
    """:class:`ParseResult` is a ``frozen=True`` dataclass."""
    w = seeded_pipeline_world

    result = await parse(
        raw_text="$305 Bunnings Kelly bluemetal",
        db=db_session,
        entered_by=w["admin"],
        expense_date=date(2026, 4, 21),
        expense_type=ExpenseType.supplier_expense,
    )

    with pytest.raises(FrozenInstanceError):
        result.review_status = ReviewStatus.pending  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Ambiguity + source provenance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ambiguous_job_matches_preserved(db_session, seeded_pipeline_world):
    """Two-job-alias input surfaces ambiguous_matches + job_uncertain."""
    w = seeded_pipeline_world

    result = await parse(
        raw_text="Kelly Smith 100",
        db=db_session,
        entered_by=w["admin"],
        expense_date=date(2026, 4, 21),
        expense_type=ExpenseType.supplier_expense,
    )

    assert result.partial.job_id is None
    assert result.partial.job_conf == 0.3
    assert result.ambiguous_job_matches == tuple(sorted([w["job_a"].job_id, w["job_b"].job_id]))
    assert ReviewReasonCode.job_uncertain in result.review_reasons


@pytest.mark.asyncio
async def test_source_per_field_provenance_all_rules(
    db_session,
    seeded_pipeline_world,
):
    """After a clean rules parse, every populated field is ``"rules"``."""
    w = seeded_pipeline_world

    result = await parse(
        raw_text="$305 Bunnings Kelly bluemetal",
        db=db_session,
        entered_by=w["admin"],
        expense_date=date(2026, 4, 21),
        expense_type=ExpenseType.supplier_expense,
    )

    p = result.partial
    assert p.source_per_field["amount"] == "rules"
    assert p.source_per_field["job"] == "rules"
    assert p.source_per_field["supplier"] == "rules"
    assert p.source_per_field["category"] == "rules"
    assert p.source_per_field["payment"] == "rules"
    assert p.source_per_field["description"] == "rules"
    assert p.source_per_field["expense_type"] == "rules"


# ---------------------------------------------------------------------------
# Diagnostic metadata on ParseResult
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_matched_via_surfaced(db_session, seeded_pipeline_world):
    """``matched_job_via`` + ``matched_supplier_via`` flow to ParseResult."""
    w = seeded_pipeline_world

    result = await parse(
        raw_text="$305 Bunnings Kelly bluemetal",
        db=db_session,
        entered_by=w["admin"],
        expense_date=date(2026, 4, 21),
        expense_type=ExpenseType.supplier_expense,
    )

    # ``Kelly`` is an alias on Job A → route ``alias``.
    assert result.matched_job_via == "alias"
    # ``Bunnings`` has no alias seeded → name route.
    assert result.matched_supplier_via == "name"
    # ``source_span`` points at the amount token.
    assert result.amount_source_span is not None
    start, end = result.amount_source_span
    assert "$305 Bunnings Kelly bluemetal"[start:end] == "305"


@pytest.mark.asyncio
async def test_duplicate_gated_when_amount_missing(
    db_session,
    seeded_pipeline_world,
):
    """No amount ⇒ duplicate detection is skipped entirely, no crash."""
    w = seeded_pipeline_world

    # No numeric token → amount_value None → duplicate pass skipped.
    result = await parse(
        raw_text="Kelly Bunnings bluemetal",
        db=db_session,
        entered_by=w["admin"],
        expense_date=date(2026, 4, 21),
        expense_type=ExpenseType.supplier_expense,
    )

    assert result.partial.amount_value is None
    assert result.partial.duplicate_flag is False
    # amount_uncertain fires because amount_value is None.
    assert ReviewReasonCode.amount_uncertain in result.review_reasons


@pytest.mark.asyncio
async def test_duplicate_gated_when_job_missing(
    db_session,
    seeded_pipeline_world,
):
    """No job_id ⇒ duplicate detection is skipped (no SELECT with None)."""
    w = seeded_pipeline_world

    # No job tokens → job_id None → duplicate pass skipped.
    result = await parse(
        raw_text="$305 Bunnings bluemetal",
        db=db_session,
        entered_by=w["admin"],
        expense_date=date(2026, 4, 21),
        expense_type=ExpenseType.supplier_expense,
    )

    assert result.partial.job_id is None
    assert result.partial.duplicate_flag is False
    assert ReviewReasonCode.job_uncertain in result.review_reasons


# ---------------------------------------------------------------------------
# Default LLM parser path — no spy, still works
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_default_llm_is_mock_and_preserves_rules(
    db_session,
    seeded_pipeline_world,
):
    """With no ``llm_parser`` supplied, the default :class:`MockLLMParser`
    is used on review-triggered inputs and returns the rules partial
    unchanged — so source_per_field stays ``"rules"`` throughout."""
    w = seeded_pipeline_world

    result = await parse(
        raw_text="工地1 163",
        db=db_session,
        entered_by=w["admin"],
        expense_date=date(2026, 4, 21),
        expense_type=ExpenseType.supplier_expense,
    )

    # Review did fire (amount_uncertain at least), so MockLLMParser
    # was engaged — but it returns the same object, so source stays
    # rules.
    assert result.review_status == ReviewStatus.pending
    assert result.partial.source_per_field.get("amount") == "rules"
    # Proves the default path doesn't crash + doesn't require a spy.
    assert isinstance(MockLLMParser(), LLMParser)
