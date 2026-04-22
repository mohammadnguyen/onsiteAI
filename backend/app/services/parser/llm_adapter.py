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

Contract
--------
A real (non-mock) implementation of :meth:`LLMParser.parse` MUST NOT
mutate ``rules_partial`` in place. It returns either the same object
(when it has nothing to add) or a new :class:`ParsePartial` whose
:attr:`ParsePartial.source_per_field` has been updated to ``"llm"``
for every field the LLM overwrote. Fields the LLM does not touch keep
their ``"rules"`` provenance. The orchestrator relies on
``source_per_field`` for downstream diagnostics and audit entries, so
skipping this bookkeeping is a correctness bug, not a style issue.
"""

from __future__ import annotations

import abc
import uuid
from dataclasses import dataclass, field
from decimal import Decimal

from app.models import ExpenseType, PaymentMethod


@dataclass
class ParsePartial:
    """Partial parse result with per-field values + confidences.

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

        Phase 2 ships only MockLLMParser, which returns rules_partial
        unchanged. A real Claude-backed implementation is Phase 2.5.
        """


class MockLLMParser(LLMParser):
    """Deterministic no-op. Phase 2 ships only this.

    Returns rules_partial unchanged (same object — callers treat the
    result as immutable for diagnostic purposes). source_per_field is
    not mutated; all fields remain "rules".

    Phase 2.5 will add ClaudeLLMParser wired behind this same interface.
    """

    async def parse(self, raw_text: str, rules_partial: ParsePartial) -> ParsePartial:
        return rules_partial
