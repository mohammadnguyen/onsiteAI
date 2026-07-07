"""LLM-ready seam for the Phase 2 parser pipeline.

The Phase 2 parser is a two-stage affair: a deterministic rules pass
produces a :class:`ParsePartial` populated from regex / alias lookups,
and an optional LLM stage may then refine low-confidence fields. This
module defines the abstract seam between the two stages so the
orchestrator (landing in Batch 2) can remain agnostic about whether a
real model or a stub is in use.

Phase 2 vs Phase 2.5
--------------------
Phase 2 ships **only** :class:`MockLLMParser`, a deterministic no-op
that returns the rules-derived partial unchanged. No network calls, no
``anthropic`` or ``openai`` dependency, no HTTP at all. Phase 2.5 adds
``ClaudeLLMParser`` wired behind this same :class:`LLMParser`
interface; the orchestrator and everything above it will not need to
change.

Parser mutation contract (FROZEN before Batch 2)
-------------------------------------------------
The rules of engagement for every piece of the Phase 2 parser:

1. **Stage functions are pure.** ``tokenize``, ``extract_amount``,
   ``match_job``, ``match_supplier``, ``match_category``,
   ``extract_payment_method``, ``detect_duplicate``, and
   ``derive_review_reasons`` each take their own inputs and return a
   narrow, stage-specific result object (e.g. ``AmountMatch``,
   ``JobMatch``). They do NOT take or return a :class:`ParsePartial`.

2. **The orchestrator is the sole constructor of ParsePartial.** It
   calls each stage, collects the narrow results, and builds exactly
   one :class:`ParsePartial` from them. ``source_per_field`` is
   populated with ``"rules"`` for every field filled from a stage
   result at construction time.

3. **ParsePartial is treated as immutable once the orchestrator
   returns it from the rules pass.** No stage — not the orchestrator,
   not the LLM adapter — may mutate a ``ParsePartial`` in place. To
   produce an updated partial, construct a new one via
   ``dataclasses.replace(partial, field=new_value, ...)``.

4. **LLMParser.parse(raw_text, rules_partial) -> ParsePartial** MUST
   NOT mutate the input. Allowed returns:
     - the same object, unchanged (identity-preserved) — MockLLMParser
       does this and it is the Phase 2 behaviour
     - a new :class:`ParsePartial` built via ``dataclasses.replace``
       with updated field values AND updated ``source_per_field``
       entries (``"llm"`` for every overwritten field) — this is
       what Phase 2.5's ClaudeLLMParser will do
   Mutating ``rules_partial`` in place is a correctness bug.

5. The orchestrator may itself call ``dataclasses.replace`` on the
   ParsePartial it built — e.g. when duplicate detection fires after
   the main stages and needs to set ``duplicate_flag`` and
   ``duplicate_of_expense_id``. Treating this as "constructing the
   next partial" rather than "mutating the previous" keeps the
   contract symmetrical.

This contract is why stage result types are narrow dataclasses (see
each stage module): they force the orchestrator to do the assembly
and keep stages independently testable.
"""

from __future__ import annotations

import abc
import uuid
from dataclasses import dataclass, field
from decimal import Decimal

from app.models import ExpenseType, PaymentMethod


@dataclass(frozen=True)
class ParsePartial:
    """Partial parse result with per-field values + confidences.

    ``frozen=True`` enforces the mutation contract below in the type system
    (audit C-3): no stage — orchestrator or LLM adapter — can reassign a
    field in place; an updated partial is produced only via
    :func:`dataclasses.replace`. This is the guardrail the Phase 2.5 real
    LLM seam relies on, so a future ``ClaudeLLMParser`` cannot silently
    corrupt the rules-derived partial.

    Phase 2 T-D ships this as a scaffold; later parser tasks flesh out
    the population logic (rules pass) and the consumers (orchestrator,
    review-queue enqueuer). The structure is intentionally flat: each
    field has a matching ``*_conf`` float in [0.0, 1.0] so downstream
    code can cheaply decide whether to route to manual review.

    ``source_per_field`` tracks the provenance of every populated field
    — ``"rules"`` if the deterministic pass wrote it, ``"llm"`` if the
    LLM stage overwrote it. The mock LLM parser never touches this
    dict; a real implementation MUST update it for any field it
    overwrites (see module docstring).
    """

    raw_text: str
    amount_value: Decimal | None = None
    amount_conf: float = 0.0
    unsupported_currency: bool = False
    job_id: uuid.UUID | None = None
    job_conf: float = 0.0
    supplier_id: uuid.UUID | None = None
    supplier_conf: float = 0.0
    candidate_supplier_name: str | None = None
    category_id: uuid.UUID | None = None
    category_conf: float = 0.0
    payment_method: PaymentMethod = PaymentMethod.unknown
    expense_type: ExpenseType = ExpenseType.supplier_expense
    description: str | None = None
    duplicate_flag: bool = False
    duplicate_of_expense_id: uuid.UUID | None = None
    source_per_field: dict[str, str] = field(default_factory=dict)


class LLMParser(abc.ABC):
    """Abstract seam between the rules pass and the LLM refinement pass."""

    @abc.abstractmethod
    async def parse(self, raw_text: str, rules_partial: ParsePartial) -> ParsePartial:
        """Refine a rules-derived ParsePartial using an LLM.

        Implementations may overwrite fields in rules_partial where the
        LLM has higher confidence than the rules did. They MUST update
        source_per_field to "llm" for any field they change.

        Safety rails (enforced by the orchestrator, audit R6): whatever a
        real implementation returns is passed through
        ``orchestrator._sanitize_llm_partial`` before use — the call is
        wrapped in a timeout with fall-back to the rules partial, all
        confidences are clamped to [0, 1], a non-finite/non-positive
        amount is discarded, and any money field the model CHANGED
        (amount or job) has its confidence forced below the review gate
        so an LLM-sourced money value can never bypass the review queue.
        Implementations therefore cannot assume a returned confidence is
        honoured verbatim for money fields.

        Phase 2 ships only MockLLMParser, which returns rules_partial
        unchanged. A real Claude-backed implementation is Phase 2.5.
        """


class MockLLMParser(LLMParser):
    """Deterministic no-op. Phase 2 ships only this.

    Returns the same ``rules_partial`` object unchanged. Because no
    fields are overwritten, ``source_per_field`` remains entirely
    ``"rules"`` and no bookkeeping is needed. Identity preservation
    (``result is rules_partial``) is an intentional, documented
    property — tests assert on it to pin the no-op behaviour.

    Phase 2.5 adds ``ClaudeLLMParser`` behind the same interface; it
    will construct new ``ParsePartial`` instances via
    ``dataclasses.replace`` rather than mutating the input. See the
    module docstring for the full contract.
    """

    async def parse(self, raw_text: str, rules_partial: ParsePartial) -> ParsePartial:
        return rules_partial
