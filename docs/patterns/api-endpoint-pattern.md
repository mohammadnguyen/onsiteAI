# API endpoint pattern

## Purpose

Shape of a new FastAPI route. Keeps RBAC, HTTP-translation, and
exception-mapping consistent across endpoints so callers (admin web,
mobile, future integrations) get the same behaviour everywhere.

## When To Use

Any new HTTP route added under `backend/app/api/`. Every endpoint —
GET, POST, PATCH, DELETE — uses this shape.

## Standard Structure

A new endpoint is added to an existing router (or a new router
registered through `backend/app/api/router.py`). The route function:

1. Depends on `get_current_user` (or `require_admin` for admin-only
   routes) for auth.
2. Depends on `get_db` for the async SQLAlchemy session.
3. Validates input via Pydantic in the function signature
   (`body: SomeCreate`).
4. Calls one function in `backend/app/services/` — never more than
   one, and never persistence code inline.
5. Wraps any domain exceptions raised by the service into
   `HTTPException` with the documented status code and the
   exception's own `detail` string.
6. Returns a Pydantic response model declared as `response_model=` on
   the decorator.

The canonical examples:

- `backend/app/api/expenses.py` — full CRUD with the exception-map.
- `backend/app/api/router.py` — central registration.

## Rules

- Routes do not perform business logic, FK validation, or persistence
  directly. They are HTTP/exception adapters.
- Every domain exception has a documented HTTP mapping:
  `ExpenseValidationError` → 422, `ExpenseNotFound` → 404,
  `JobNotFound` → 404, `JobNotFoundForExpense` → 422,
  `EditForbidden` / `DeleteForbidden` → 403. New domain exceptions
  document their mapping when introduced.
- `response_model` is declared explicitly on every route.
- `status_code` is declared explicitly on every non-default route
  (e.g. `status.HTTP_201_CREATED` on POST routes that create).
- Async dependencies only. No sync DB calls. No blocking I/O in a
  route function.
- A route never imports from another router. Cross-cutting helpers
  live in `backend/app/api/deps.py` or a service.

## Anti-Patterns

- Raw SQL or `db.execute(...)` calls inside a route function.
- Business validation in the route ("if amount > X return 422 …") —
  push it down to a service-layer validator.
- Swallowing exceptions and returning a 200 with an error in the body.
- Returning 200 from a creation endpoint instead of 201.
- Missing `response_model` (forces clients to guess the shape).
- Calling `db.commit()` or `db.flush()` from a route — the service
  owns transaction boundaries.

## Testing Expectations

- Integration test via the `client` fixture in
  `backend/tests/conftest.py`. Tests POST/PATCH/DELETE through the
  full HTTP stack, not against the service in isolation.
- Each test asserts:
  - the response status code (explicitly, not "< 400"),
  - the response body shape (key fields, not full equality),
  - the side-effect on the DB (rows created, updated, soft-deleted,
    queue rows added, audit rows written).
- No HTTP-layer unit tests in isolation. If logic is worth unit-
  testing without a request, that logic belongs in a service and
  should be tested there.
- A new endpoint ships with: happy-path test, at least one 4xx
  failure test, at least one RBAC test (contributor blocked or
  admin-only enforcement). Add regression tests inline when a
  trial-evidence finding produces a fix; name the test after the
  finding (e.g. `test_chp1_…`).

Canonical test files: `backend/tests/test_expenses_api.py`,
`backend/tests/test_review_queue_api.py`.
