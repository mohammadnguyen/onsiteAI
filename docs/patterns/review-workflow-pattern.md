# Review workflow pattern

## Purpose

How the review queue is created, viewed, resolved, and rejected. The
queue is the human-review surface for any AI-derived or
contributor-submitted value that the system cannot accept blindly.

## When To Use

Any feature where the parser or an AI step may produce an uncertain
result, or where an admin needs to confirm a value before persistence
is final. New review reasons go through this pattern; new state
transitions on the queue itself need an ADR.

## Standard Structure

**At capture (write path):**

1. The expense is persisted with `review_status='pending'`.
2. A single `ExpenseReviewQueue` row is inserted in the **same
   transaction**, with a non-empty `review_reasons` array of
   `ReviewReasonCode` values.
3. Both writes succeed or both fail. There is no half-state.

**At triage (admin read + edit):**

4. Admin lists open items via `GET /review-queue?status=open`.
5. Admin opens detail. They may edit the expense via `PATCH
   /expenses/{id}`. Only fields in `_AUDITABLE_FIELDS` are editable
   (`supplier_id`, `category_id`, `amount_inc_gst`,
   `expense_date`, `description`, `notes`, `payment_method`,
   `receipt_status`). **`job_id` is not editable post-creation.**
6. Edits to a row whose pre-state was `reviewed` write an
   `ExpenseAuditLog` row recording the pre-image and post-image.
   Edits to `pending` rows do not — the queue itself is the audit.

**At resolution (admin closes the item):**

7. `POST /review-queue/{review_id}/resolve` with optional
   `resolution_notes`:
   - the queue row's `status` flips to `resolved`,
   - the expense's `review_status` flips to `reviewed`,
   - an audit row records the transition (actor, timestamp,
     resolution notes).

**At rejection (admin discards the item):**

8. `POST /review-queue/{review_id}/reject` with optional
   `resolution_notes`:
   - the queue row's `status` flips to `rejected`,
   - the expense's `review_status` flips to `rejected` (soft
     delete; the row stays in `expenses` for auditability),
   - an audit row records the rejection.

Canonical examples:

- `backend/app/api/review_queue.py` — the endpoint surface.
- `backend/app/services/review_queue.py` — the service.
- `backend/app/models/review_queue.py` — the model + FK + unique +
  check constraints.

## Rules

- Every `pending` expense has **exactly one open** queue row. The
  `uq_expense_review_queue_expense_id` unique constraint enforces
  this at the DB level.
- A queue row cannot exist without its parent expense row. The FK is
  `nullable=False`.
- `review_reasons` is non-empty. The
  `ck_expense_review_queue_reasons_non_empty` check constraint
  enforces this.
- Resolution **writes an audit row**. Same for rejection. The audit
  log is append-only.
- `job_id` is immutable post-creation. If the parser attributed the
  wrong job, the expense must be rejected and re-entered, not
  patched. This is why ambiguous-job captures are rejected at the
  API edge (CHP-1 / 2) rather than being saved with `job_uncertain`.
- Rejected expenses are excluded from subsequent duplicate-detection
  scans (`backend/app/services/parser/duplicates.py` filters out
  `ReviewStatus.rejected`).

## Anti-Patterns

- Writing a queue row whose `expense_id` does not exist yet (FK
  violation at flush time, or worse, a pre-insert sneaks through).
- Closing a queue row by `DELETE`ing it. Queue rows stay forever;
  their `status` field carries the history.
- Setting `expense.review_status='reviewed'` directly via `PATCH
  /expenses/{id}` without going through the queue's resolve
  endpoint — bypasses the audit-row write.
- A queue row with zero `review_reasons` (DB rejects it; do not work
  around the check by inserting a dummy reason).
- Storing the rejection's "why" in the expense's `notes` field
  instead of the queue row's `resolution_notes`. The queue is the
  audit surface.

## Testing Expectations

- API integration tests use the `client` fixture, hitting the real
  routes through HTTP. Tests assert all four side-effects on
  resolve/reject:
  - expense `review_status` transition,
  - queue row `status` transition,
  - audit-row presence and content,
  - downstream effect (e.g. rejected rows excluded from duplicate
    scans).
- A new review reason ships with: a parser test that triggers it on
  the right input, an API test that confirms it appears in the queue
  row's `review_reasons`, and an end-to-end test that resolves the
  queue item without error.
- The duplicate-detection regression test
  (`test_chp3_duplicate_detection_fires_via_api` in
  `backend/tests/test_expenses_api.py`) is the canonical full-path
  pattern: submit twice through `POST /expenses`, assert the second
  carries `duplicate_of_expense_id` and the `duplicate_suspected`
  review reason, and assert the queue row carries the reason in its
  array.

Canonical test file: `backend/tests/test_review_queue_api.py`.
