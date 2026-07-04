# Backend Code Audit — SiteTracker / Budget AI

Maximum-depth, read-only audit of the FastAPI service in `backend/`.
HEAD `b80edd9`. Date 2026-07-04. Method: Phase-0 ground-truth execution
(live Postgres, real test run) → five parallel specialist slices reading every
in-scope file line-by-line with live runtime probing → adversarial red-team
cross-examination → this synthesis. Every finding cites a contract clause
(`CLAUDE.md`, ADRs 0001–0004) or is tagged `[BEYOND-CONTRACT]`. No source file
was modified.

Slice + red-team work ran on Claude Opus 4.8 (~1.0M tokens, 248 tool calls,
6 agents). Money/parser/concurrency claims were verified against the running DB
by the slices and the load-bearing High findings were re-verified independently
during synthesis.

---

## 0. Remediation status (applied 2026-07-04, same session)

All findings in this report were subsequently **fixed** (§4 master table) after
operator approval. Summary of the remediation:

- **All 9 High findings + E1** and every Med/Low fixed, except **C-2** which
  was implemented (mixed Arabic+CJK: `100元`, `5千`, `3万5` now parse) — the
  ambiguous multi-digit tail (`3万50`) is intentionally left routing to review.
- **Two additive, reversible migrations** added: `e4b1c9d27f30`
  (GST-sum + non-negative money CHECKs — T-1/B-4) and `f2c3d4e5a6b7`
  (review-queue partial-open unique index — D-6/T-2). New head `f2c3d4e5a6b7`;
  full `upgrade → downgrade base → re-upgrade` verified; **zero model↔migration
  drift** (column/CHECK/index diffs all empty).
- The GST invariant is now enforced by one authoritative reconciler
  (`models/expense.reconcile_gst_split`) feeding every write path, backstopped
  by the DB CHECK; the two admin/labour races take row/advisory locks; bcrypt
  runs off the event loop; the reset script is env-guarded; `/auth/login` is
  rate-limited; docs are prod-gated; `exc.orig` no longer leaks.
- **Tests: 964 → 1009 passing** (45 added, including real two-transaction
  concurrency tests for the races). **`ruff check`: 167 → 0** (behaviour-safe
  autofixes + config opt-outs for the FastAPI `Depends` idiom and forward-ref
  quoting). The prior `SAWarning` is gone (D-5).

The verdict below (§2) describes the code **as audited**; with the above
remediation applied it no longer holds — the disqualifying gaps are closed.

---

## 1. Toolchain execution (verbatim)

**Environment.** `uv` is not installed on this Windows host and the project venv
has no `pip`, so `uv sync` could not run; the pre-existing populated venv at
`D:\SITE TRACKER 888888888888\backend\.venv` (Python 3.12.10) was used. It is
lock-complete — the suite runs clean — so this is a host-tooling gap, not a
dependency gap. **DB: the Docker `sitetracker-db` container (postgres:16, host
port 5433) was already running before the audit began** and was left running
(see Phase 4). `pg_isready` → `accepting connections`; `sitetracker_test`
present.

### `ruff check app tests` (ruff 0.15.11, project's own pyproject config) — FAILS

```
101  B008    [ ] function-call-in-default-argument
 23  UP037   [*] quoted-annotation
 17  I001    [*] unsorted-imports
 10  UP042   [ ] replace-str-enum
  4  E501    [ ] line-too-long
  4  SIM300  [*] yoda-conditions
  2  F401    [*] unused-import
  2  SIM117  [*] multiple-with-statements
  2  UP047   [ ] non-pep695-generic-function
  1  UP017   [*] datetime-timezone-utc
  1  UP035   [*] deprecated-import
Found 167 errors.
[*] 50 fixable with the `--fix` option (12 hidden fixes can be enabled with the `--unsafe-fixes` option).
```

101 of the 167 are `B008` (`Depends(...)`/`Query(...)` in argument defaults),
which is the *idiomatic* FastAPI pattern — the ruff config selects `B` but does
not add the conventional `B008` per-file-ignore for the API layer. As committed,
the lint gate fails; this is a config/style issue, not a correctness one. The
remaining 66 are cosmetic (import sorting, quoted annotations, str-enum
modernization). None are bugs.

### `python -m pytest -q` (against live `sitetracker_test`) — PASSES

```
........................................................................ [ 97%]
............................                                             [100%]
============================== warnings summary ===============================
tests/test_jobs.py::test_create_job_duplicate_code_returns_409
tests/test_jobs.py::test_patch_partial_threshold_violating_db_check_returns_422
  sys:1: SAWarning: transaction already deassociated from connection

tests/test_jobs.py::test_patch_partial_threshold_violating_db_check_returns_422
  ...fastapi\routing.py:328: DeprecationWarning: 'HTTP_422_UNPROCESSABLE_ENTITY'
  is deprecated. Use 'HTTP_422_UNPROCESSABLE_CONTENT' instead.

964 passed, 3 warnings in 201.08s (0:03:21)
```

**964 tests ran and passed against the live Postgres test DB** (0 failed, 0
skipped, 0 collection errors). The two `SAWarning`s are a real signal, not
noise — see **D-5**: they expose that `get_db`'s commit/rollback contract is
never exercised by the suite. The `DeprecationWarning` is a harmless FastAPI
constant rename.

### Independent DB verification (synthesis agent, throwaway DBs only)

- **Alembic chain**: single head `a7c4e2f10d3b`, 13-node linear graph, **fully
  reversible** — `upgrade head → downgrade base → re-upgrade head` runs clean and
  lands on head; downgrades leave only `alembic_version` and drop every enum type.
- **Zero model/migration drift**: the metadata-built schema (what tests use) and
  the Alembic-built schema (what production runs) are **byte-identical** on
  columns, check constraints, and indexes. ADR 0001's "schema always matches the
  current models" holds.
- **No float anywhere in the money layer**: a schema-wide scan for
  `double precision`/`real` columns returned nothing — every money column is
  `numeric` (amounts `numeric(12,2)`, rates `numeric(8,2)`, hours `numeric(4,2)`,
  percentages `numeric(5,2)`). Any float risk is in Python arithmetic, not storage.
- **`expenses` has zero CHECK constraints** (confirmed via `pg_constraint`): no
  `amount >= 0`, no `inc = ex + gst`. This is the DB-level root of the B-/X-/T-1
  money cluster below.

---

## 2. Executive verdict

**No — not safe to trust with real money as-is, but the gap is a short, fixable
list, not an architectural one.** The foundation is genuinely strong: Decimal
discipline is clean end-to-end, the migration chain is linear/reversible/drift-
free, tenancy and authorization are server-enforced (not client courtesy) with a
uniform 401 chain and a consistently-applied contributor money-strip, no secret
is in git history or logs, and the Excel accountant export is fully formula-
injection-hardened. The disqualifying problems are specific and concentrated: the
GST component invariant `inc = ex + gst` is **enforced nowhere authoritative**, so
three separate write paths (structured create, lone-component PATCH, and the
reviewer-resolve path — which additionally ignores the cash rule) can silently
persist internally-contradictory money that corrupts the very job dashboards and
accountant Excel totals the product exists to produce. Alongside that sit an
**unguarded `reset_testing_expenses.py` that will wipe production expenses +
audit trail if pointed at the wrong `DATABASE_URL`**, an **unthrottled public
`/auth/login`** with a 1-character-password floor, and two concurrency races (an
admin-lockout-to-zero and a labour daily-allocation double-count). Fix the nine
High findings and E1 — most are a few lines each, and a single
`CHECK(amount_ex_gst + gst_amount = amount_inc_gst)` collapses the entire money
cluster — and this backend is safe to run the business on.

---

## 3. Authorization matrix (Subagent A, full)

Legend: **A**=admin, **C**=contributor, **U**=unauthenticated. `401`=uniform
not-authenticated; `403`=admin-only; `own`=own/eligible rows only; `strip`=money
nulled for contributors. There is **no `system` role** in the codebase — the
`UserRole` enum is `{admin, contributor}` only (the shared map's "system" was
inaccurate; corrected here).

| Route | Method | Auth dep | A | C | U | Verdict |
|---|---|---|---|---|---|---|
| /auth/login | POST | public | 200 | 200 | 200 (bad→401) | OK |
| /auth/me | GET | get_current_user | 200 | 200 | 401 | OK |
| /auth/refresh | POST | public; re-checks is_active | 200 | 200 | 401/422 | OK |
| /auth/logout | POST | get_current_user | 204 | 204 | 401 | OK (stateless no-op) |
| /users | GET | require_admin | 200 | 403 | 401 | OK |
| /users/invite | POST | require_admin | 201 | 403 | 401 | OK (race A-2→D-3) |
| /users/{id} | PATCH | require_admin | 200 | 403 | 401 | OK (race A-1/D-3) |
| /categories | GET | get_current_user | 200 | 200 | 401 | OK |
| /categories | POST | require_admin | 201 | 403 | 401 | OK |
| /categories/{id} | PATCH | require_admin | 200 | 403 | 401 | OK |
| /expenses | POST | get_current_user | 201 | 201 strip | 401 | OK |
| /expenses/parse | POST | get_current_user | 200 | 200 | 401 | OK (draft carries no money) |
| /expenses | GET | get_current_user | 200 all | 200 own+strip | 401 | OK (mine server-forced) |
| /expenses/{id} | GET | get_current_user | 200 | 200 own+strip / 403 | 401 | OK |
| /expenses/{id} | PATCH | get_current_user | 200 | 200 own-pending / 403 | 401 | OK |
| /expenses/{id} | DELETE | require_admin | 204 | 403 | 401 | OK |
| /expenses/{id}/audit | GET | require_admin | 200 | 403 | 401 | OK |
| /jobs | POST | require_admin | 201 | 403 | 401 | OK |
| /jobs | GET | get_current_user | 200+summary | 200 strip | 401 | OK |
| /jobs/{id} | GET | get_current_user | 200 detail | 200 strip | 401 | OK |
| /jobs/{id}/budget-summary | GET | require_admin | 200 | 403 | 401 | OK |
| /jobs/{id} | PATCH | require_admin | 200 | 403 | 401 | OK |
| /jobs/{id} | DELETE | require_admin | 204 | 403 | 401 | OK |
| /jobs/{id}/audit | GET | require_admin | 200 | 403 | 401 | OK |
| /jobs/{id}/aliases | POST | require_admin | 201 | 403 | 401 | OK |
| /jobs/{id}/category-budgets | POST | require_admin | 201 | 403 | 401 | OK |
| /jobs/{id}/category-budgets/{bid} | PATCH | require_admin | 200 | 403 | 401 | OK (pair-atomic 404) |
| /jobs/{id}/category-budgets/{bid} | DELETE | require_admin | 204 | 403 | 401 | OK (pair-atomic 404) |
| /workers | GET | get_current_user | 200 | 200 rate-strip | 401 | OK |
| /workers | POST | require_admin | 201 | 403 | 401 | OK |
| /workers/{id} | PATCH | require_admin | 200 | 403 | 401 | OK |
| /labour-entries/batch | POST | get_current_user | 201 | 201 own+today | 401 | OK |
| /labour-entries | GET | get_current_user | 200 | 200 | 401 | OK (site-presence, by design) |
| /labour-entries/{id} | DELETE | get_current_user | 204 | 204 own+today / 403 | 401 | OK |
| /labour-summary | GET | require_admin | 200 | 403 | 401 | OK (money) |
| /labour-rollup | GET | get_current_user | 200 full | 200 cost/hours-null | 401 | OK |
| /reports/expenses-excel | GET | require_admin | 200 | 403 | 401 | OK |
| /healthz | GET | none | 200 | 200 | 200 | OK (open by design) |

All `U` cells were probed to return `401` across 22 routes: `require_admin`
never fires a 403 before authentication because `get_current_user` is the inner
dependency. IDOR was probed clean on every by-id contributor path (expenses
`_owns()`, labour `_can_modify()`, category-budgets validated as a
`(job_id, budget_id)` pair). `mine=1` is **server-enforced** — a contributor
omitting it still sees only their own rows. `/auth/refresh` re-checks `is_active`
so a deactivated user's refresh token cannot mint access tokens. The single
authz defect is the concurrency race (A-1/D-3), below.

---

## 4. Master findings table (severity, then confidence)

23 confirmed findings after adjudication (2 killed as duplicates, 3 demoted, 2
new cross-slice, 2 net-new from the top-3 hunt, 1 hypothesis refuted). A-1 and
D-3 are the **same code defect** (unlocked admin count) surfaced by two slices —
counted once, at its worst (High) severity.

| # | ID | Sev | Conf | File:line | One-line |
|---|---|---|---|---|---|
| 1 | T-1 | **High** | Certain | models/expense.py:219 | GST invariant `inc=ex+gst` enforced nowhere authoritative — no DB CHECK, root of the whole money cluster |
| 2 | B-1 | **High** | Certain | services/expenses.py:261 | Structured create with both ex+gst supplied persists them verbatim, no sum check |
| 3 | B-2 | **High** | Certain | services/expenses.py:1119 | Lone-component PATCH (`gst_amount` OR `amount_ex_gst`) skips recompute → inconsistent triple |
| 4 | X-1 | **High** | Certain | services/review_queue.py:296 | Reviewer-resolve recomputes GST with hardcoded 1/11, ignoring `payment_method` → cash rows get phantom GST |
| 5 | C-1 | **High** | Certain | services/parser/cjk_amounts.py:160 | Malformed CJK (`十十块`,`一万千块`) → plausible amount @conf 0.9 → skips review queue |
| 6 | D-1 | **High** | Certain | core/security.py:57 | bcrypt (~350ms) runs synchronously on the event loop in login/invite — no threadpool |
| 7 | D-2 | **High** | Certain | services/labour.py:281 | Cross-job ≤1.0/day rule: `FOR UPDATE` locks nothing on first insert → concurrent double-allocation |
| 8 | E1 | **High** | Certain | scripts/reset_testing_expenses.py:45 | Unguarded `DELETE FROM expenses` (cascades queue+audit) against whatever `DATABASE_URL` points at |
| 9 | A-1/D-3 | **High** | Certain | services/users.py:157 | Admin count read-then-write race, no lock → two concurrent demotions → **zero admins** (lockout) |
| 10 | B-3 | Med | Certain | services/expenses.py:255 | Cash=GST-exclusive rule bypassable by a structured cash expense with explicit `gst_amount` |
| 11 | B-4 | Med | Certain | models/expense.py:219 | DB lacks non-negative CHECKs on expense/job money that Pydantic implies |
| 12 | X-2 | Med | Certain | services/review_queue.py:291 | Reviewer-resolve has the same lone-component gap as B-2 + no sum re-check |
| 13 | E2 | Med | Certain | api/auth.py:49 | No rate limiting on `/auth/login` or `/auth/refresh` (public fly.dev URL) |
| 14 | E3 | Med | Certain | schemas/user.py:47 | Invite accepts a 1-character password (`min_length=1`) |
| 15 | C-4 | Med | Certain | services/parser/orchestrator.py:168 | Zero pipeline/confidence/decision logging in the flagship parser |
| 16 | D-5 | Med | Certain | tests/conftest.py:103 | `get_db` commit/rollback path is untested; the SAWarning is a poisoned-fixture tell |
| 17 | T-2 | Med | Likely | services/expenses.py:1228 | Review lifecycle is one-way: a resolved/rejected expense can never be re-queued → future 500 |
| 18 | E4 | Low | Certain | api/jobs.py:152 | `/jobs` create+update leak raw `exc.orig` Postgres text into the 422 body |
| 19 | E5 | Low | Certain | main.py:44 | `/docs`,`/redoc`,`/openapi.json` served in every env incl. production |
| 20 | C-3 | Low | Certain | services/parser/llm_adapter.py:74 | `ParsePartial` not frozen despite the "immutable" contract clause (latent; Mock-only today) |
| 21 | C-5 | Low | Likely | services/parser/dates.py:113 | Year-default for year-less dates uses UTC server clock — Sydney new-year edge |
| 22 | C-6 | Low | Likely | services/parser/amount.py:112 | `>2`-decimal amount accepted @conf 1.0, silently rounded by `NUMERIC(12,2)` |
| 23 | C-2 | Low | Likely | services/parser/cjk_amounts.py:217 | `3万5`,`5千`,`100元` extract no amount (fail-safe to review; capture-speed miss) |
| 24 | D-6 | Low | Likely | models/review_queue.py:118 | Unconditional `UNIQUE(expense_id)` (not partial-on-open) blocks future re-queue |
| 25 | D-7 | Low | Likely | database.py:28 | No `pool_pre_ping`/`pool_recycle` → stale Fly connection → first-request 500 after idle |
| 26 | E6 | Low | Likely | fly.toml:47 | Manual-migrate (correct per ADR-0003) but no startup head-check → silent app/schema drift |

Killed: **A-2** (dup of D-3), **B-5** (dup of D-6). Refuted: **T-3**
(auth-bypass hypothesis — role & is_active are reloaded live per request).
Demoted: **C-2, C-3, D-4** (D-4 dropped off the table as a Low performance smell;
see §6/appendix).

---

## 5. Findings by slice (with diff-shaped fixes)

### The money-integrity cluster (T-1, B-1, B-2, B-3, X-1, X-2, B-4) — the headline

The `inc = ex + gst` invariant holds by construction **only** on the derived
path (`compute_gst_split` computes `gst` as the remainder). Every path that
accepts caller-supplied components can persist an inconsistent triple, and
`budget_summary` + the Excel export sum the three money columns **independently**,
so a violated triple shows internally-contradictory totals to the operator and
the accountant. **T-1 is the systemic root: there is no DB CHECK**, so each fix
below patches one Python gate while the category stays open for the next path.

**T-1 — add the one backstop that closes every current and future path
(`models/expense.py` + migration):**
```diff
  __table_args__ = (
+     CheckConstraint(
+         "amount_ex_gst + gst_amount = amount_inc_gst",
+         name="ck_expenses_gst_components_sum",
+     ),
+     CheckConstraint(
+         "amount_inc_gst >= 0 AND amount_ex_gst >= 0 AND gst_amount >= 0",
+         name="ck_expenses_amounts_nonneg",          # also closes B-4
+     ),
      Index(...),  # existing indexes unchanged
  )
```
Add the matching additive migration. This converts B-1/B-2/X-1/X-2 and any 4th
path from *silent corruption of accountant-facing money* into an `IntegrityError`
the service maps to 422.

**B-1 — validate the both-supplied branch (`services/expenses.py:261`):**
```diff
-     return amount_ex, gst
+     if amount_ex + gst != amount_inc:
+         raise ExpenseValidationError("amount_ex_gst + gst_amount must equal amount_inc_gst")
+     return amount_ex, gst
```

**B-2 — recompute on any money-field change (`services/expenses.py:1119`):**
```diff
-     if (
-         ("amount_inc_gst" in patch_set or "payment_method" in patch_set)
-         and "amount_ex_gst" not in patch_set
-         and "gst_amount" not in patch_set
-     ):
-         ex, gst = compute_gst_split(expense.amount_inc_gst, expense.payment_method)
+     if patch_set & {"amount_inc_gst", "payment_method", "amount_ex_gst", "gst_amount"}:
+         ex, gst = compute_gst_split(expense.amount_inc_gst, expense.payment_method)
          expense.amount_ex_gst, expense.gst_amount = ex, gst
```

**X-1 — make the reviewer path payment-aware (`services/review_queue.py:296`),
verified independently: it hardcodes `_GST_DIVISOR` while `update_expense:1124`
correctly calls `compute_gst_split(inc, payment_method)`:**
```diff
-         ex = (expense.amount_inc_gst / _GST_DIVISOR).quantize(Decimal("0.01"))
-         expense.amount_ex_gst = ex
-         expense.gst_amount = expense.amount_inc_gst - ex
+         expense.amount_ex_gst, expense.gst_amount = compute_gst_split(
+             expense.amount_inc_gst, expense.payment_method
+         )
```
Also broaden its gate (line 291) to `payment_method`/lone-component changes (X-2),
identical shape to the B-2 fix. Probe: `compute_gst_split(100, cash) → (100.00,
0.00)` vs the current `(90.91, 9.09)`.

**B-3 — force the cash rule server-side regardless of overrides
(`services/expenses.py:255`):**
```diff
  def _compute_gst_split(amount_inc, amount_ex, gst, payment_method):
+     if payment_method == PaymentMethod.cash:
+         return compute_gst_split(amount_inc, payment_method)   # ex=inc, gst=0.00
      if amount_ex is None and gst is None:
          return compute_gst_split(amount_inc, payment_method)
```

**B-4 — covered by the second CHECK in T-1** (plus `budget_amount_ex_gst >= 0`,
`contract_value_ex_gst >= 0`, `total_budget_ex_gst >= 0` on their tables). The
asymmetry is stark: `labour_entries` already has `hours`, `rate_snapshot`,
`hourly_rate`, and `day_fraction` CHECKs; the money columns on `expenses`/`jobs`
have none.

### Slice A — AuthZ & tenancy

**A-1 / D-3 — serialize the admin-count check with a row lock
(`services/users.py`); the naive `with_for_update()` on an aggregate `COUNT` is
invalid SQL, so lock rows and count them:**
```diff
- stmt = select(func.count()).select_from(User).where(User.role==admin, User.is_active.is_(True))
- count = (await db.execute(stmt)).scalar_one()
+ stmt = select(User.user_id).where(User.role==admin, User.is_active.is_(True)).with_for_update()
+ count = len((await db.execute(stmt)).scalars().all())
```
Apply before both the demotion/deactivation guard (`<= 1`) and the invite/promote
cap (`>= limit`). Live-DB probe confirmed two READ COMMITTED transactions each
read count=2 and both commit → 0 admins. Everything else in Slice A verified
solid (15 items) — server-enforced scoping, uniform 401, refresh re-check,
complete money-strip coverage, pair-atomic budget IDOR protection.

### Slice C — Parser pipeline

**C-1 — reject malformed CJK place-marker sequences
(`services/parser/cjk_amounts.py`), the one path where a *money* value is trusted
without review:**
```diff
+     if last_place is not None and place >= last_place:
+         return None   # 十十 / ascending-or-repeated place is malformed
      accumulator += pending_digit * place
```
Probe: `十十块 → 20 @0.9`, `一万千块 → 11000 @0.9`; `amount_uncertain` only fires
below conf 0.8, so these save as `reviewed` with no queue row. Magnitude handling
itself is correct (`三万五 = 35000`, not 305). **C-4** (no parser logging),
**C-5** (UTC year default), **C-6** (>2-decimal silent round), **C-3**
(`ParsePartial` should be `@dataclass(frozen=True)`), **C-2** (Arabic+CJK forms):
fixes in the appendix data. Day-first date parsing, duplicate-window boundaries,
and review-threshold operators all verified solid.

### Slice D — Async & transactions

**D-1 — offload bcrypt to a worker thread (`services/auth.py` /
`services/users.py`):**
```diff
- if not verify_password(password, user.password_hash):
+ import anyio
+ if not await anyio.to_thread.run_sync(verify_password, password, user.password_hash):
      return None
```
Measured 348ms verify / 349ms hash on this venv; `async def` routes get no
threadpool from FastAPI, so a burst of logins serializes on the loop and freezes
every other in-flight request.

**D-2 — cover the not-yet-existing rows with a per-worker+date advisory lock
(`services/labour.py`), since `FOR UPDATE` on the existing-rows SELECT locks
nothing on the first insert:**
```diff
+ await db.execute(select(func.pg_advisory_xact_lock(_lock_key(worker_id, work_date))))
  locked_rows = ... with_for_update() ...
```
`uq_labour_entries_worker_job_date` is on `(worker, job, date)` so two concurrent
batches into *different* jobs don't collide → a worker gets 2.0 days on one date.

**D-5 — fix the test fixture so the real `get_db` commit path is exercised
(`tests/conftest.py`):** use `AsyncSession(bind=conn,
join_transaction_mode="create_savepoint")` and add one no-override integration
test driving the real `get_db` against a throwaway DB. This silences the SAWarning
at its source and closes the untested-commit gap. (The warning itself is a
fixture artifact — production `get_db` rolls back cleanly on `IntegrityError`.)

**D-6 / T-2 — make the review-queue uniqueness partial (`models/review_queue.py`
+ migration):**
```diff
- UniqueConstraint("expense_id", name="uq_expense_review_queue_expense_id")
+ Index("uq_expense_review_queue_one_open", "expense_id", unique=True,
+       postgresql_where=text("status = 'open'"))
```
Matches ADR 0001's stated "one *open* row per expense" and unblocks a supported
re-review path instead of a designed-in `IntegrityError` dead-end. **D-7**
(`pool_pre_ping=True, pool_recycle=1800`) is a two-line engine change. Commit
topology verified solid: zero mid-request commits, every multi-write path atomic.

### Slice E — Config, secrets & ops

**E1 — guard the destructive reset (`scripts/reset_testing_expenses.py`):**
```diff
+ from app.config import get_settings
+ if get_settings().app_env not in {"development", "test"}:
+     raise SystemExit(f"refusing destructive reset against APP_ENV={get_settings().app_env!r}")
```

**E2** — add a per-IP + per-email throttle on `/auth/login` and `/auth/refresh`
(slowapi or an in-process token bucket; single-node `min_machines_running=1`
makes in-process viable). Record the decision as an ADR (auth change). **E3** —
`initial_password: str = Field(min_length=12, max_length=255)`. **E4** — replace
`detail=f"Database constraint violated: {exc.orig}"` with a static message at
`api/jobs.py:152` and `:309`, log `exc.orig` server-side. **E5** — gate
`docs_url`/`redoc_url`/`openapi_url` on `app_env in {development, test}`. **E6** —
add a non-fatal `schema_head_matches=<bool>` startup log (do **not** add
`release_command`; manual migration is correct per ADR-0003).

**Verified clean with runtime evidence** (do not re-investigate): git history
carries only `.env.*.example` templates (no real secret ever committed;
`backend/.env` gitignored and absent); the startup log and error handler emit
booleans/paths/exc-type only; a forced asyncpg connection failure does **not**
leak the DB password/user/db through the `exc_info` path; `/auth/refresh`
re-checks `is_active`; HS256 is pinned as a list (no `alg=none` downgrade);
`pyjwt` (not vulnerable `python-jose`); `python-multipart>=0.0.26`; Excel export
formula-injection escaping covers `= + - @ \t \r` on every user-controlled cell
with RFC 5987 dual `Content-Disposition`.

---

## 6. Top 5 by risk-reduction-per-line-changed

1. **T-1 — one `CHECK(amount_ex_gst + gst_amount = amount_inc_gst)`** (≈3 lines +
   a migration). Converts B-1, B-2, X-1, X-2, and every future write path from
   silent corruption of accountant-facing money into a catchable 422. Highest
   leverage in the entire audit.
2. **E1 — a 3-line `APP_ENV` guard** on the reset script. Prevents irreversible
   destruction of all production expenses + the append-only audit trail.
3. **X-1 — a 3-line swap to `compute_gst_split(inc, payment_method)`** in
   `review_queue.resolve`. Stops cash expenses corrected during review from
   acquiring a phantom GST component.
4. **A-1/D-3 — a 2-line change to lock the active-admin rows** before counting.
   Closes the concurrent-demotion path to a permanent zero-admin lockout.
5. **D-1 — wrap `verify_password`/`hash_password` in `anyio.to_thread.run_sync`**
   (≈2 lines per call site). Removes a ~350ms event-loop stall on every login.

Honorable mention: **E3** is a literal one-character change (`min_length=1` →
`12`) that materially raises the auth floor and compounds the E2 mitigation.

---

## 7. Red-team appendix — killed, demoted, refuted

**Killed (duplicates):**
- **A-2** — cap-exceed direction of the same unlocked admin-count race as **D-3**
  (whose own scenario names concurrent over-cap promotions). Same file, root
  cause, and fix; no distinct code location.
- **B-5** — exact duplicate of **D-6** (one constraint,
  `uq_expense_review_queue_expense_id`; same partial-index fix).

**Demoted (real but lower-severity at this scale):**
- **C-2 Med→Low** — `3万5`/`5千`/`100元` probe to `(None, 0.0)`, which fires
  `amount_uncertain` and routes to review. Fail-safe capture-speed miss, not
  mis-valuation; AI-rules ("don't invent values") not violated.
- **C-3 Med→Low** — `ParsePartial` is mutable, but Phase 2 ships only
  `MockLLMParser` (identity no-op) and the orchestrator uses `dataclasses.replace`.
  Latent guardrail gap, zero live blast radius until a real LLM is wired.
- **D-4 Med→Low** — Excel export is `O(jobs × ~6)` queries + synchronous
  `wb.save` on the loop, but it's an admin-only on-demand download for a locked
  3-user single-tenant product: seconds of latency, not an outage. (Batch via
  `summarize_jobs` and wrap `wb.save` in `to_thread` when convenient.)

**Refuted (chased, does not hold):**
- **T-3 auth-bypass hypothesis** — a demoted/deactivated user retaining access.
  `deps.get_current_user` reloads the `User` row every request and checks
  `is_active` live; `require_admin` reads `role` from that same fresh row. Both
  deactivation and role demotion are reflected within one request. The only
  residual is non-revocable refresh tokens (no `jti` denylist), which is an
  explicitly documented Phase-6 deferral, not a live bug.

**New from the cross-slice / top-3 hunt:** X-1, X-2 (reviewer-path money bugs a
single-file slice structurally could not see), and the T-1 framing (the money
cluster's shared DB-level root cause). The red-team also confirmed the A-1/D-3
fix sketches are sound only after correcting `with_for_update()`-on-`COUNT` to
row-selection-then-`len()`.

**Known-good, re-verified, NOT re-flagged:** env fail-fast validation
(placeholder/short JWT secret, wildcard CORS), JWT access/refresh `type`
discriminator + `jti`, uniform-401 dependency chain, secrets-never-logged startup
discipline, bcrypt-via-passlib.
