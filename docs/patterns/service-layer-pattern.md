# Service layer pattern

## Purpose

Where business logic, validation, and persistence live. The service
layer is the single source of truth for "what is true" in the system.
Routes adapt HTTP to it; tests exercise it directly.

## When To Use

Any backend function that reads or writes the database, or applies a
business rule (validation, state transitions, computed fields,
cross-table invariants). If the function does not touch the DB and
does not enforce a rule, it belongs in `app/core/` or `app/services/
parser/`, not in the service layer.

## Standard Structure

A service function is an `async def` that:

1. Takes the DB session as the first positional argument:
   `db: AsyncSession`.
2. Takes typed keyword-only arguments for the operation
   (`*, expense_id: uuid.UUID, …`).
3. Returns the persisted model row (or a small computed result
   dataclass), or raises one of the module's documented domain
   exceptions.
4. Performs validation **before** persistence
   (`_validate_save(...)`).
5. Performs FK pre-checks **before** persistence
   (`_validate_fk_refs(...)`), raising a domain exception with a
   useful `detail` string. Do not let the DB throw
   `IntegrityError` to the caller.
6. Writes audit rows where the documented rule requires them
   (admin edits to `reviewed` rows; state transitions).
7. Owns its transaction boundary. The route does not commit; the
   service does, via `db.flush()` / context manager exits.

Canonical examples:

- `backend/app/services/expenses.py` — `create_expense`,
  `update_expense`, `_validate_save`, `_validate_fk_refs`.
- `backend/app/services/budget_summary.py` — `summarize_job`,
  `summarize_jobs`.

## Rules

- No Pydantic schemas in service signatures. Use plain
  Python types (`uuid.UUID`, `Decimal`, `date`, model classes). The
  API layer converts to/from `BaseModel`.
- No `HTTPException` in services. Raise a domain exception; let the
  route map it.
- Dependencies flow one direction: `app/api/` → `app/services/` →
  `app/models/` / `app/core/` / `app/services/parser/`. Service A
  may not call service B which calls service A.
- A service that mutates two tables uses one explicit transaction.
  Half-written state on failure is a bug.
- Logging on critical paths follows the rules in `CLAUDE.md` §5 and
  the expanded list in `docs/patterns/ai-output-pattern.md` for AI
  steps.

## Anti-Patterns

- Raising `HTTPException` from a service function.
- Catching a domain exception and returning `None` (silently swallows
  failures; the caller cannot distinguish "not found" from "found but
  empty").
- Service A imports service B which imports service A. Break the
  cycle via a shared `core` helper or by inverting one direction.
- Writing two tables without an explicit transaction boundary.
- Re-implementing validation logic that already lives in a Pydantic
  field (`gt=0`, `le=10_000_000`, `max_length=…`) — let the Pydantic
  layer reject malformed input before the service runs.
- Performing presentation logic (i18n string selection, formatting
  money, choosing a chip colour) in a service.

## Testing Expectations

- Test directly against the `db_session` fixture in
  `backend/tests/conftest.py`. Each test runs inside a transaction
  that rolls back at teardown — no cross-test contamination.
- Service tests assert on:
  - the returned model state,
  - DB row state (queryable via the same `db_session`),
  - audit-row presence and content for paths that should write
    audit,
  - queue-row state for paths that interact with the review queue.
- Service tests do not go through the HTTP layer. If the assertion
  needs an HTTP status code, the test belongs in the API-test file.
- A new service function ships with: happy-path test, validation-
  failure test, FK-failure test, and (where applicable) audit-row
  test. Add regression tests when a trial finding fixes a bug — name
  the test after the finding.

Canonical test files: `backend/tests/test_budget_summary_service.py`,
`backend/tests/test_seed_suppliers.py`. The parser stages have their
own pattern (see `ai-output-pattern.md`).
