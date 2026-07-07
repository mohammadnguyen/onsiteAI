"""Phase 2 Task T-K: the parser orchestrator.

Public entry point for the Phase 2 parser. Given a free-text expense
string plus the caller context (DB session, acting user, expense date
and type), runs every stage in order and returns a
:class:`ParseResult` carrying:

* the fully-populated :class:`~app.services.parser.llm_adapter.ParsePartial`
* the derived :class:`~app.models.ReviewStatus` + tuple of
  :class:`~app.models.ReviewReasonCode` values (the review decision)
* diagnostic metadata surfaced through to the API: ambiguous match
  tuples from the job / supplier stages, the original-text span of the
  amount match, and the ``matched_via`` route strings from the
  job / supplier stages.

Why this is the ONLY place ParsePartial gets constructed
--------------------------------------------------------
The frozen Phase 2 parser mutation contract (see
:mod:`app.services.parser.llm_adapter`) says stage functions are pure
and return narrow, stage-specific result dataclasses
(``AmountMatch``, ``JobMatch``, …). The orchestrator is the sole
constructor of :class:`ParsePartial`; it calls each stage, takes the
narrow results, and assembles a single ParsePartial at step 8 below
with ``source_per_field`` populated as ``"rules"`` for every field it
fills from a stage result.

Subsequent "updates" to that partial (the duplicate-detection pass in
step 10, any LLM overrides in step 9) are produced via
:func:`dataclasses.replace` — the orchestrator never mutates a partial
in place, and neither does the :class:`~app.services.parser.llm_adapter.LLMParser`
contract.

Pure w.r.t. DB writes
---------------------
:func:`parse` runs ``SELECT`` queries through the supplied
:class:`~sqlalchemy.ext.asyncio.AsyncSession` (for the job / supplier /
category / duplicate stages) but never writes. The expenses service
layer is responsible for persisting the expense + enqueuing the review
row after inspecting the :class:`ParseResult`.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    ExpenseType,
    ReviewReasonCode,
    ReviewStatus,
    User,
)
from app.services.parser import amount as _amount
from app.services.parser import categories as _categories
from app.services.parser import cjk_amounts as _cjk_amounts
from app.services.parser import duplicates as _duplicates
from app.services.parser import jobs as _jobs
from app.services.parser import payment as _payment
from app.services.parser import review as _review
from app.services.parser import suppliers as _suppliers
from app.services.parser import tokens as _tokens
from app.services.parser.llm_adapter import LLMParser, MockLLMParser, ParsePartial

# Pipeline observability (audit C-4). One structured decision line per parse —
# confidences + review decision + reason codes only. NEVER logs the raw user
# text (it can carry business content); confidences and reason codes are enough
# to debug a real mis-parse or a wrongly-(un)gated review.
_log = logging.getLogger("app.parser")

# Mirrors the ``description`` column limit (``String(500)``) and the
# ``ExpenseCreate.description`` Pydantic ``max_length``. Kept local so the
# parser has no import dependency on the ORM model.
_MAX_DESCRIPTION_LEN = 500

# --- LLM seam safety rails (audit R6) ---
# Hard wall-clock cap on the optional LLM refinement call so a provider
# hang can't stall expense capture on a job site.
_LLM_TIMEOUT_S = 8.0
# Review gates (must match app.services.parser.review): an LLM that
# CHANGES a money-affecting field can never carry confidence at/above its
# gate — an LLM-sourced amount/job always routes to a human reviewer.
_AMOUNT_REVIEW_GATE = 0.8
_JOB_REVIEW_GATE = 0.7


def _clamp_conf(v: object) -> float:
    """Coerce a confidence to a finite float in [0.0, 1.0] (0.0 on garbage)."""
    try:
        f = float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    if f != f:  # NaN
        return 0.0
    return max(0.0, min(1.0, f))


def _valid_amount(value: object) -> bool:
    """True iff an LLM-supplied amount is a safe, finite, positive Decimal."""
    if value is None:
        return True
    if not isinstance(value, Decimal):
        return False
    return value.is_finite() and value > 0


def _sanitize_llm_partial(rules: ParsePartial, llm: ParsePartial) -> ParsePartial:
    """Constrain an LLM-returned partial so model output can't corrupt money
    or bypass review (audit R6).

    * Non-:class:`ParsePartial` or a nonsensical amount (NaN/Inf/non-Decimal/
      <= 0) → drop the model's amount and keep the rules-derived one.
    * All confidences are clamped to ``[0, 1]``.
    * Any money field the LLM CHANGED (amount or job) has its confidence
      forced below the review gate, so an LLM-sourced money value always
      routes to a human — the model may fill/lower confidence, never raise
      it past the gate.
    """
    if not isinstance(llm, ParsePartial):
        return rules
    if not _valid_amount(llm.amount_value):
        llm = dataclasses.replace(
            llm, amount_value=rules.amount_value, amount_conf=rules.amount_conf
        )
    amount_conf = _clamp_conf(llm.amount_conf)
    job_conf = _clamp_conf(llm.job_conf)
    if llm.amount_value != rules.amount_value:
        amount_conf = min(amount_conf, _AMOUNT_REVIEW_GATE - 0.01)
    if llm.job_id != rules.job_id:
        job_conf = min(job_conf, _JOB_REVIEW_GATE - 0.01)
    return dataclasses.replace(
        llm,
        amount_conf=amount_conf,
        job_conf=job_conf,
        supplier_conf=_clamp_conf(llm.supplier_conf),
        category_conf=_clamp_conf(llm.category_conf),
    )


@dataclass(frozen=True)
class ParseResult:
    """Final orchestrator output.

    Carries the fully-populated :class:`ParsePartial` plus the derived
    review decision (status + reason codes) and diagnostic metadata
    (ambiguous matches, matched_via, source span for the amount) that
    the API layer will surface back to clients.
    """

    partial: ParsePartial
    review_status: ReviewStatus
    review_reasons: tuple[ReviewReasonCode, ...]
    ambiguous_job_matches: tuple[uuid.UUID, ...] = ()
    ambiguous_supplier_matches: tuple[uuid.UUID, ...] = ()
    amount_source_span: tuple[int, int] | None = None
    matched_job_via: str | None = None
    matched_supplier_via: str | None = None


def _derive_description(tokens: list[_tokens.Token]) -> str | None:
    """Build the advisory description field from the token stream.

    Phase 2 rule (documented, deterministic, intentionally loose):
    concatenate the original-case ``text`` of every token that is
    neither a currency symbol nor numeric-like. This keeps description
    construction trivially testable — the richer "drop tokens consumed
    by matched job / supplier / category" variant was explicitly
    rejected in the task spec. Returns ``None`` for an empty result so
    the ParsePartial stores an explicit null rather than an empty
    string.
    """
    parts = [tok.text for tok in tokens if not tok.is_currency_symbol and not tok.is_numeric_like]
    if not parts:
        return None
    # Bound to the ``description`` column limit (String(500)). Raw input
    # can be up to 2000 chars, so an un-truncated join would overflow the
    # column and 500 on write (audit R17). The description is advisory,
    # so a hard clip is safe.
    return " ".join(parts)[:_MAX_DESCRIPTION_LEN]


async def parse(
    *,
    raw_text: str,
    db: AsyncSession,
    entered_by: User,
    expense_date: date,
    expense_type: ExpenseType = ExpenseType.supplier_expense,
    llm_parser: LLMParser | None = None,
) -> ParseResult:
    """Parse ``raw_text`` through the full Phase 2 pipeline.

    Orchestration steps (fixed order):

    1. Tokenize.
    2. Amount extraction (pure).
    3. Job matching (DB read).
    4. Supplier matching (DB read). Always run regardless of
       ``expense_type`` — the review-reason deriver gates the
       ``supplier_uncertain`` trigger on expense type internally.
    5. Category matching (DB read).
    6. Payment-method extraction (pure).
    7. Description: all non-currency / non-numeric token texts joined
       with single spaces (see :func:`_derive_description`).
    8. Assemble the initial :class:`ParsePartial` with
       ``source_per_field`` populated as ``"rules"`` for every field
       filled from a stage result.
    9. If any review reason fires, hand the partial to the configured
       :class:`LLMParser` (defaulting to :class:`MockLLMParser`) and
       use whatever it returns as the new "current partial".
    10. Duplicate detection — only when both ``job_id`` and
        ``amount_value`` are populated. On a hit, construct an updated
        partial via :func:`dataclasses.replace` with ``duplicate_flag``
        set and ``duplicate_of_expense_id`` populated. Never mutate.
    11. Re-derive review reasons on the possibly-updated partial.
    12. ``review_status`` = :attr:`ReviewStatus.pending` iff reasons is
        non-empty, else :attr:`ReviewStatus.reviewed`.
    13. Return a :class:`ParseResult`.

    Pure w.r.t. DB writes — stages use the session only for
    ``SELECT``. The expenses service layer persists after inspecting
    the result.

    Parameters
    ----------
    raw_text:
        Free-text expense string (e.g. ``"$305 Bunnings Kelly bluemetal"``).
    db:
        Async SQLAlchemy session used for the DB-backed stages.
    entered_by:
        The :class:`User` submitting the expense. Accepted for future
        per-user hints (Phase 3+); Phase 2 does not read user state.
    expense_date:
        The calendar date the caller is recording against. Used by the
        duplicate detector's ±1 day window.
    expense_type:
        Defaults to :attr:`ExpenseType.supplier_expense`. Labour /
        adjustment drafts opt out of supplier-uncertain review.
    llm_parser:
        Optional :class:`LLMParser` injection for testing. Phase 2
        defaults to :class:`MockLLMParser`.
    """
    # Step 1: tokenize.
    tokens = _tokens.tokenize(raw_text)

    # Step 1.5: Capture Parser v1 — CJK amount normalization. Rewrites
    # Simplified Chinese numeral tokens (五百五, 一千二, 五百块, …)
    # into the existing Token shape with is_numeric_like=True so the
    # downstream amount extractor sees them as ordinary numeric
    # candidates. Pure function; preserves source spans; no DB. See
    # :mod:`app.services.parser.cjk_amounts` module docstring for the
    # supported forms, safety gates, and confidence semantics.
    tokens = _cjk_amounts.normalize_cjk_amount_tokens(tokens)

    # Step 2: amount (pure).
    amt = _amount.extract_amount(tokens)

    # Steps 3–5: DB-backed matchers.
    # Pass amt.source_span to the job matcher so the token consumed as
    # the amount is excluded from job lookup — prevents a bare numeric
    # amount from silently assigning the expense to a job whose code
    # happens to equal that number. Operator guardrail; see
    # _word_normals in jobs.py.
    job = await _jobs.match_job(tokens, db, excluded_span=amt.source_span)
    sup = await _suppliers.match_supplier(tokens, db)
    cat = await _categories.match_category(tokens, db)

    # Step 6: payment method (pure).
    pay = _payment.extract_payment_method(tokens)

    # Step 7: description.
    description = _derive_description(tokens)

    # Step 8: assemble ParsePartial. ``source_per_field`` records
    # ``"rules"`` for each field populated from a stage result at
    # construction time; per the mutation contract, a Phase 2.5
    # ClaudeLLMParser would flip those entries to ``"llm"`` for any
    # fields it overwrites.
    source_per_field: dict[str, str] = {
        "amount": "rules",
        "job": "rules",
        "supplier": "rules",
        "category": "rules",
        "payment": "rules",
        "description": "rules",
        "expense_type": "rules",
    }
    partial = ParsePartial(
        raw_text=raw_text,
        amount_value=amt.value,
        amount_conf=amt.confidence,
        unsupported_currency=amt.unsupported_currency,
        job_id=job.job_id,
        job_conf=job.confidence,
        supplier_id=sup.supplier_id,
        supplier_conf=sup.confidence,
        candidate_supplier_name=sup.candidate_supplier_name,
        category_id=cat.category_id,
        category_conf=cat.confidence,
        payment_method=pay,
        expense_type=expense_type,
        description=description,
        duplicate_flag=False,
        duplicate_of_expense_id=None,
        source_per_field=source_per_field,
    )

    # Step 9: LLM seam. Only engage if any review reason fires on the
    # rules-derived partial. Per the mutation contract, the returned
    # partial may be the same object (MockLLMParser) or a replace()'d
    # new one (future ClaudeLLMParser). Either way, whatever we get
    # back becomes the new current partial.
    pre_llm_reasons = _review.derive_review_reasons(partial)
    if pre_llm_reasons:
        llm = llm_parser if llm_parser is not None else MockLLMParser()
        rules_partial = partial
        try:
            llm_result = await asyncio.wait_for(
                llm.parse(raw_text, rules_partial), timeout=_LLM_TIMEOUT_S
            )
        except Exception as exc:
            # An LLM outage / timeout / crash must never fail or hang expense
            # capture. Fall back to the deterministic rules partial, which
            # already carries its own review reasons (audit R6).
            _log.warning("llm_parse_failed_fell_back error=%s", type(exc).__name__)
            llm_result = rules_partial
        if llm_result is rules_partial:
            # No-op refinement (MockLLMParser identity) — keep as-is.
            partial = rules_partial
        else:
            # Real model output: constrain it so it can't corrupt money or
            # raise confidence past the review gate.
            partial = _sanitize_llm_partial(rules_partial, llm_result)

    # Step 10: duplicate detection — gated on populated job + amount.
    # Labour / adjustment drafts without a job id or an amount cannot
    # produce a meaningful duplicate query. On a hit we build a NEW
    # partial via replace() rather than mutating the existing one.
    if partial.job_id is not None and partial.amount_value is not None:
        dup = await _duplicates.detect_duplicate(
            db=db,
            job_id=partial.job_id,
            amount_inc_gst=partial.amount_value,
            expense_date=expense_date,
            supplier_id=partial.supplier_id,
            description=partial.description,
        )
        if dup.found:
            partial = dataclasses.replace(
                partial,
                duplicate_flag=True,
                duplicate_of_expense_id=dup.duplicate_of_expense_id,
            )

    # Step 11: re-derive reasons on the (possibly updated) partial.
    reasons = _review.derive_review_reasons(partial)

    # Step 12 (A1b): only MONEY-integrity reasons gate to ``pending`` + a
    # review-queue row. Supplier / category-only uncertainty is
    # non-blocking enrichment, so the expense saves as ``reviewed`` with
    # no queue row. The queue row (when opened) carries only these gating
    # reasons, so the active review queue is money-driven.
    gating = _review.gating_reasons(reasons)
    review_status = ReviewStatus.pending if gating else ReviewStatus.reviewed

    _log.info(
        "parse_complete review_status=%s amount_conf=%.2f job_conf=%.2f "
        "supplier_conf=%.2f category_conf=%.2f duplicate=%s reasons=%s",
        review_status.value,
        partial.amount_conf,
        partial.job_conf,
        partial.supplier_conf,
        partial.category_conf,
        partial.duplicate_flag,
        [r.value for r in gating],
    )

    # Step 13: assemble + return.
    return ParseResult(
        partial=partial,
        review_status=review_status,
        review_reasons=tuple(gating),
        ambiguous_job_matches=job.ambiguous_matches,
        ambiguous_supplier_matches=sup.ambiguous_matches,
        amount_source_span=amt.source_span,
        matched_job_via=job.matched_via,
        matched_supplier_via=sup.matched_via,
    )
