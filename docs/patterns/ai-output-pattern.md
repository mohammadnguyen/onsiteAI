# AI output pattern

## Purpose

How AI-derived values flow into business data without bypassing human
review. AI in this repo is **assistive, not authoritative**.
Deterministic systems (the DB, the parser's rules pass, the review
queue) remain the source of truth.

## When To Use

Any new code path that consumes parser output, LLM output, or a
confidence-weighted classifier result. Today this is the parser
pipeline only; a real LLM is not yet wired (`MockLLMParser` is the
current implementation).

## Standard Structure

1. An AI extraction stage is a `async def` function that takes
   narrow inputs (tokens, DB session) and returns a narrow result
   dataclass with at minimum `confidence: float` and a `matched_via`
   tag. Examples: `JobMatch`, `AmountMatch`, `SupplierMatch`,
   `DuplicateMatch`.
2. The orchestrator
   (`backend/app/services/parser/orchestrator.py`) runs the stages
   in order and assembles a single `ParsePartial`. The orchestrator
   is the **sole** constructor of `ParsePartial`; stage functions
   never touch it.
3. The review-reason deriver emits a list of `ReviewReasonCode`s
   based on the `ParsePartial`'s confidences and gaps.
4. The expense service decides:
   - `review_status='reviewed'` when no review reasons fired,
   - `review_status='pending'` plus a review-queue row when one or
     more reasons fired,
   - raise `ExpenseValidationError` (HTTP 422) when the parser
     cannot resolve a required value (e.g. no job match at all,
     ambiguous job match per CHP-1).
5. The pending expense and its queue row are written in the **same
   transaction**.

Canonical examples:

- `backend/app/services/parser/orchestrator.py` — the pipeline.
- `backend/app/services/parser/jobs.py` — a stage with a confidence
  + `matched_via` + `ambiguous_matches` return.
- `backend/app/services/parser/duplicates.py` — a soft-match stage.
- `backend/app/services/expenses.py:create_expense` — the service
  that merges parser output with caller overrides and decides
  review status.

## Rules

- AI is assistive. Deterministic systems remain the source of truth.
  When the parser disagrees with the user's explicit input, the
  user wins (caller-set fields take precedence per `model_fields_
  set`).
- Confidence < 0.95 must surface a review reason. Confidence == 0.0
  with no candidates and no caller-set value means **hard rejection**
  at the API edge (the CHP behaviour): HTTP 422 with an actionable
  detail, zero side-effects on `expenses` or `expense_review_queue`.
- AI must never silently overwrite a caller-set field. The merge
  order is: parser draft → caller's `model_fields_set` overrides →
  validation.
- AI output that affects a financial total
  (`amount_inc_gst`, `job_id`, `supplier_id`, `gst_amount`,
  `expense_date`) must either be saved with `review_status='pending'`
  and a queue row, OR rejected outright. There is no third option.
- When a real LLM replaces `MockLLMParser`, the call site logs:
  - prompt content as a stable hash (not the raw content) so
    repeated identical prompts can be correlated without leaking
    PII or financial detail,
  - model identifier and version,
  - request latency,
  - response payload (subject to redaction rules — a future ADR).
- LLM calls live inside the parser pipeline, not in route handlers.

## Anti-Patterns

- Picking a "best guess" when the parser returns ambiguous
  candidates. CHP-1 explicitly forbids this — `job_id` is immutable
  post-creation, so a wrong attribution is a permanent error.
- Mutating a `ParsePartial` in place. Construct a new one via
  `dataclasses.replace(...)`.
- Calling the LLM from a route handler.
- Using AI output to short-circuit validation. Even high-confidence
  AI output goes through `_validate_save` and `_validate_fk_refs`.
- Caching AI output for one user and reusing it for another. Today
  the parser is per-request; do not introduce a shared cache without
  an ADR.
- Letting an AI step decide to silently delete or soft-delete a
  business row. Rejection happens via the review queue (`reject`
  endpoint), not via AI inference.

## Testing Expectations

- Golden-input tests for each parser stage under
  `backend/tests/parser/test_*_matcher.py`. Each test pins one input
  → one expected narrow result (confidence, matched_via,
  ambiguous_matches).
- Edge cases: empty input, currency-only input, numeric-only input,
  bare CJK token, multi-word match, ambiguous match, no match.
- CHP regression tests assert both the 422 status and the verbatim
  `detail` string. The detail string is part of the contract the
  capture UI relies on.
- Full-API-path tests for behaviours that cross more than one
  stage. Duplicate detection's canonical test is
  `test_chp3_duplicate_detection_fires_via_api` in
  `backend/tests/test_expenses_api.py` — it submits identical raw
  input twice and asserts the second response carries
  `duplicate_of_expense_id` and the `duplicate_suspected` review
  reason. Parser-only tests are insufficient for this.
- When wiring a real LLM, the test suite gets a deterministic mock
  (the current `MockLLMParser` shape is the pattern). No test should
  hit a real LLM endpoint.
