# Phase 3 Lite — dashboard / budget visibility

> **Branch B is the recommended next phase by operator decision, not by automatic framework trigger.** The framework's hard trigger phrase fires on a confirmed `visibility-gap >= 3` count; the trial used an off-system (Option B) issue log and no `visibility-gap` count was tallied for Claude. The recommendation rests on three observable facts:
>
> 1. **Operator instruction:** the user explicitly directed "move to the next phase" at window-close (2026-05-01).
> 2. **Capture is not materially breaking daily use:** trial completed 7 days with one open queue item; resolution times of 9 sec to 1 min for the rest; no rollback or production incidents.
> 3. **No Branch A threshold cleared from available telemetry:** no field-level audit edits (so no parser-miss evidenced by audit), one duplicate flag and it was a true positive, no non-AUD inputs. Branch A's three thresholds (≥ 5 parser-miss, ≥ 10 alias-gap, ≥ 3 description polish triggers) all remained un-met on what the system can observe; `alias-gap` and `review-friction` weren't independently tallied.
>
> Therefore Branch B is the recommended direction by elimination + operator direction + the user's stated business priority for visibility from before the trial opened. This plan covers only the seven sections inside the locked Branch B scope. Anything else (Claude fallback, review queue expansion, Excel export, iOS/TestFlight, attachments, notifications, labour attendance) is out of scope and ships in a separate phase.

**Goal:** Answer the question *"am I over budget on this job?"* without doing any math by hand. Surface per-job and per-category actual-vs-budget directly on the existing `/jobs` and `/jobs/:id` admin pages, sorted so the highest-risk job lands at the top.

**Architecture:** Additive on Phase 1 + Phase 2. Zero new tables. Zero new enums. Zero new migrations. One new aggregation service + one new endpoint. Two existing admin pages get richer data display. Reuses `expenses` (Phase 2 — `amount_inc_gst`, `amount_ex_gst`, `gst_amount`, `review_status`), `jobs.total_budget_ex_gst` + `jobs.contract_value_ex_gst` (Phase 1), `job_category_budgets.budget_amount_ex_gst` (Phase 1), `categories.category_name` (Phase 1).

**Tech stack additions:** none.

---

## Reuse from earlier phases

| Earlier artefact | Path | Reused for |
|---|---|---|
| `expenses` table with `amount_inc_gst`, `amount_ex_gst`, `gst_amount`, `review_status`, `job_id`, `category_id` | `backend/app/models/expense.py` (Phase 2) | All actual-spend aggregation |
| `jobs.total_budget_ex_gst`, `jobs.contract_value_ex_gst` | `backend/app/models/job.py` (Phase 1) | Job-level ex-GST budget; contract value reserved for full Phase 3 (margin) |
| `job_category_budgets.budget_amount_ex_gst` | `backend/app/models/job_category_budget.py` (Phase 1) | Per-category ex-GST budget for the category table |
| `categories` table | `backend/app/models/category.py` (Phase 1) | Category names for display |
| Service → API thin-HTTP pattern with domain exceptions | `backend/app/services/{jobs,expenses}.py` + `backend/app/api/{jobs,expenses}.py` | New `services/budget_summary.py` + extended `api/jobs.py` |
| `require_admin` dep | `backend/app/deps.py` (Phase 1) | Both new routes — Phase 3 Lite is admin-only |
| TanStack Query hooks pattern | `admin/src/api/hooks/{useJobs,useExpenses}.ts` | New `useBudgetSummary` hook |
| AppShell + admin navigation | `admin/src/components/AppShell.tsx` | No new routes, just enriched existing pages |
| OpenAPI types regen | `pwsh scripts/gen-types.ps1` | Run once after the new endpoint lands |
| Test fixtures | `backend/tests/conftest.py` + `backend/tests/fixtures.py` (Phase 2) | `seeded_admin`, `seeded_contributor`, `seed_categories`, plus a new `seeded_job_with_budget` fixture |

---

## Data model

**No new tables.** Phase 3 Lite is read-only aggregation over Phase 1 + Phase 2 data.

**Inclusion / exclusion rule:** aggregations include expenses where `review_status IN ('reviewed', 'pending')` and exclude `review_status = 'rejected'`. Phase 2's soft-delete flow sets `rejected`, so excluding it matches the existing semantic of "the expense has been retracted." `pending` is included because the framework defers reviewed-vs-pending banding to full Phase 3 — for Lite, the worst-case answer ("how much could we owe?") is what answers "am I over budget?". Banding layers in later.

**Budget math is ex-GST throughout. inc-GST is a separate display field for cash-paid totals.** Phase 1 stores both job-level and category-level budgets ex-GST (`total_budget_ex_gst`, `budget_amount_ex_gst`); comparing actual ex-GST against ex-GST budgets is apples-to-apples. The cash-payment GST rule from earlier (cash → `gst_amount = 0`, `amount_ex = amount_inc`) is already absorbed in `amount_ex_gst` per row, so the aggregate is correct without special-casing payment method.

**Job-level math:**

| Field | Computation | Notes |
|---|---|---|
| `actual_inc_gst` | `SUM(expenses.amount_inc_gst)` over non-rejected expenses on the job | Display-only; not used for budget compare |
| `actual_ex_gst` | `SUM(expenses.amount_ex_gst)` over the same set | Used for budget compare |
| `gst_amount` | `SUM(expenses.gst_amount)` over the same set | Derived; equals `actual_inc_gst − actual_ex_gst` |
| `total_budget_ex_gst` | `jobs.total_budget_ex_gst` | May be NULL or zero |
| `remaining_ex_gst` | `total_budget_ex_gst − actual_ex_gst` | NULL when budget is NULL or zero |
| `percent_consumed` | `100 * actual_ex_gst / total_budget_ex_gst` | NULL when budget is NULL or zero |
| `overspend` | `actual_ex_gst > total_budget_ex_gst` when budget is set; otherwise `False` | |

**Category-level math** (same `(reviewed, pending)` filter; budgets and actuals both ex-GST):

| Field | Computation |
|---|---|
| `actual_ex_gst` per category | `SUM(expenses.amount_ex_gst)` for non-rejected expenses on `(job_id, category_id)` |
| `budget_ex_gst` | `job_category_budgets.budget_amount_ex_gst` (NULL when no budget row exists) |
| `remaining_ex_gst` | `budget_ex_gst − actual_ex_gst` (NULL when no budget set) |
| `overspend` | `actual_ex_gst > budget_ex_gst` when budget set; otherwise `False` |

---

## API endpoints

Two endpoints. Both admin-only. Both read-only.

All field names below match the corrected schema (`total_budget_ex_gst`, `contract_value_ex_gst`, `budget_amount_ex_gst`). Example numbers use the real current state of the `晶晶` job in the dev DB so the math can be verified end-to-end.

### 1. `GET /jobs` (extended)

Existing endpoint. Add a `summary` field to each row in the response. `summary` may be present even when `total_budget_ex_gst` is NULL or zero — `remaining_ex_gst` and `percent_consumed` simply come back as `null` in that case (UI renders "—").

```json
{
  "job_id": "3c51556a-f2fa-403e-b62c-18e96bf60417",
  "job_code": null,
  "job_name": "晶晶",
  "site_address": "19 noble ave strathfield",
  "status": "active",
  "total_budget_ex_gst": "120000.00",
  "contract_value_ex_gst": "171600.00",
  "summary": {
    "actual_inc_gst": "9344.00",
    "actual_ex_gst": "9308.18",
    "gst_amount": "35.82",
    "total_budget_ex_gst": "120000.00",
    "remaining_ex_gst": "110691.82",
    "percent_consumed": "7.76",
    "overspend": false
  }
}
```

Default ordering remains alphabetical by `job_name`. The UI does the % consumed sort client-side (5–20 jobs is fine; full Phase 3 can move to backend ordering).

### 2. `GET /jobs/{job_id}/budget-summary` (new)

```json
{
  "job_id": "3c51556a-f2fa-403e-b62c-18e96bf60417",
  "actual_inc_gst": "9344.00",
  "actual_ex_gst": "9308.18",
  "gst_amount": "35.82",
  "total_budget_ex_gst": "120000.00",
  "remaining_ex_gst": "110691.82",
  "percent_consumed": "7.76",
  "overspend": false,
  "categories": [
    {
      "category_id": "…",
      "category_name": "Demolition",
      "actual_ex_gst": "8000.00",
      "budget_ex_gst": null,
      "remaining_ex_gst": null,
      "overspend": false
    },
    {
      "category_id": "…",
      "category_name": "Waste / Skip Bin",
      "actual_ex_gst": "950.00",
      "budget_ex_gst": null,
      "remaining_ex_gst": null,
      "overspend": false
    },
    {
      "category_id": "…",
      "category_name": "Concrete",
      "actual_ex_gst": "358.18",
      "budget_ex_gst": null,
      "remaining_ex_gst": null,
      "overspend": false
    }
  ]
}
```

Categories list rule: include every category that has either (a) a budget row in `job_category_budgets`, or (b) at least one non-rejected expense on this job. Categories with neither are omitted (no zero-zero rows). `budget_ex_gst = null` when a category has spend but no budget — UI shows the actual but renders the budget cell as "—" and a `No budget` chip.

---

## UI

Two existing admin pages get enriched. **No new routes. No new top-level nav.**

**GST-basis labels are made explicit everywhere money appears so the user never has to guess.** Five labels are used, exactly as written:

- `Spent inc GST` — sum of `amount_inc_gst` (display only; what was paid in cash terms)
- `Spent ex GST` — sum of `amount_ex_gst` (compared against the budget)
- `Budget ex GST` — `total_budget_ex_gst` at job level, or `budget_amount_ex_gst` at category level
- `Remaining ex GST` — `Budget ex GST − Spent ex GST`
- `% consumed` — `100 × Spent ex GST / Budget ex GST`

No label is shortened to "Spent" or "Budget" alone. Where the column would otherwise feel cramped, layouts can stack lines (see below) but the words `inc GST` / `ex GST` always appear adjacent to the number.

### `/jobs` (existing — `admin/src/pages/Jobs.tsx`)

Current state: simple table of jobs with name, code, status. Click → `/jobs/:id`.

Phase 3 Lite changes:

- New columns:
  - **Spent inc GST** — column header reads exactly `Spent inc GST`
  - **Spent ex GST** — column header reads exactly `Spent ex GST`
  - **Budget ex GST** — column header reads exactly `Budget ex GST`
  - **Remaining ex GST** — column header reads exactly `Remaining ex GST`
  - **% consumed** — value plus the banded chip (`On track` / `Approaching` / `Over budget` / `No budget`)
- For tablet widths or narrow displays, the two `Spent` columns may stack into a single column with two lines (line 1: "$9,344.00 inc GST", line 2: "$9,308.18 ex GST"); the labels still appear, just inline. The four desktop columns remain the default.
- Status chip thresholds: green `On track` (% < 80), amber `Approaching` (80 ≤ % < 100), red `Over budget` (% ≥ 100), neutral `No budget` when `total_budget_ex_gst` is NULL or zero.
- Default sort: % consumed descending (highest-risk job first), with NULL-budget rows last and a tie-break on alphabetical `job_name`.
- Empty / zero-budget rows: "—" in `Remaining ex GST` / `% consumed`; chip reads `No budget`.
- Existing job-status filter and search remain.
- Row click still navigates to `/jobs/:id`.

### `/jobs/:id` (existing — `admin/src/pages/JobDetail.tsx`)

Current state: shows job header (name, code, status, budget value), aliases, category budgets list, and a Phase 1 expenses preview.

Phase 3 Lite changes:

- **New KPI header row** (above the existing tabbed sections): four tiles, each with the explicit label as its title:
  - **`Spent inc GST`** — primary number, secondary line shows `+ GST $35.82` if `gst_amount > 0`, otherwise omitted
  - **`Spent ex GST`** — primary number
  - **`Budget ex GST`** — primary number; "—" with hint `No budget set` if NULL
  - **`Remaining ex GST`** — primary number with the same banded chip as the list page; "—" + `No budget` chip if NULL
- A small `% consumed` pill sits to the right of the four tiles (or below them on narrow widths), showing the percent + banded chip.
- **New "Budget vs actual" panel** (or tab, depending on existing layout): one row per category from the `categories` array of the new endpoint —
  - Column headers: **`Category`** · **`Actual ex GST`** · **`Budget ex GST`** · **`Remaining ex GST`** · **`Status`** (chip column)
  - Sort: % consumed desc among rows with budgets; budget-less rows go last (sub-sorted alphabetically by category_name)
  - Empty state: "No expenses on this job yet" if `categories` is empty; "No category budgets set" if every row has `budget_ex_gst = null` (rows still render with their actual values + `No budget` chip; the empty-state line appears as a hint above the table)
- Existing alias / category-budget edit forms stay; this is additive.

Localization: the new strings (`Spent inc GST`, `Spent ex GST`, `Budget ex GST`, `Remaining ex GST`, `% consumed`, `On track`, `Approaching`, `Over budget`, `No budget`, `No budget set`, `Budget vs actual`, `Actual ex GST`, `Status`, `Category`, `No expenses on this job yet`, `No category budgets set`) ship in `admin/src/i18n/{en,zh}.json`.

---

## Test strategy

### Backend unit tests (pure)

`backend/tests/services/test_budget_summary.py`:

- **Empty job** — no expenses, no category budgets → `actual_inc_gst = 0`, `actual_ex_gst = 0`, `gst_amount = 0`, `categories = []`
- **Reviewed + pending mixed** — both included in actual sums; verify the "Lite includes pending" rule
- **Rejected rows excluded** — adding a rejected expense does NOT move any total
- **All expenses rejected** — actual sums = 0; `remaining_ex_gst = total_budget_ex_gst`
- **Cash-payment GST** — entries with `payment_method='cash'` already have `gst_amount=0` and `amount_ex=amount_inc`; aggregation produces `gst_amount=0` total when the only spend is cash, no special-casing required
- **Mixed cash + transfer** — `gst_amount` total equals sum of per-row `gst_amount`; `actual_inc_gst − actual_ex_gst == gst_amount` invariant holds
- **NULL `total_budget_ex_gst`** — `remaining_ex_gst` and `percent_consumed` return `None`, `overspend = False`
- **`total_budget_ex_gst = 0`** — same as NULL: `remaining_ex_gst = None`, `percent_consumed = None`, `overspend = False` (no divide-by-zero)
- **Per-category split** — category with budget but no expenses included with `actual_ex_gst = 0`; category with expenses but no budget included with `budget_ex_gst = null` and `remaining_ex_gst = null`; category with neither omitted
- **Category overspend math** — `actual_ex_gst` exactly equal to `budget_ex_gst` → `overspend = False`; one cent over → `overspend = True`
- **Decimal precision** — all totals come back as `Decimal('0.01')`-quantized; no float drift on combinations of cash and transfer rows

### Backend integration tests (real Postgres)

`backend/tests/api/test_jobs_summary_api.py`:

- `GET /jobs` returns `summary` field on every row (admin token)
- `GET /jobs/{id}/budget-summary` happy path with mixed-status expenses + multiple categories
- 404 on unknown job_id
- 403 for contributor token on both endpoints
- 401 with no token
- Decimal precision — totals match `Decimal('0.01')` rounding (no float drift)

### Frontend

Manual E2E via Claude Preview (existing pattern). Specifically:

- Admin logs in → `/jobs` shows 晶晶 row with `Spent inc GST = $9,344.00`, `Spent ex GST = $9,308.18`, `Budget ex GST = $120,000.00`, `Remaining ex GST = $110,691.82`, `% consumed = 7.76%`, green `On track` chip.
- Click 晶晶 → KPI header tiles show the four labelled values above; the `Spent inc GST` tile shows the secondary line `+ GST $35.82`.
- Category panel shows three rows: Demolition $8,000.00 / Waste / Skip Bin $950.00 / Concrete $358.18, each with `Budget ex GST = —` and a `No budget` chip (since the 晶晶 job has zero category budgets set as of today).
- Set Demolition's `Budget ex GST` to $5,000.00 via the existing category-budget form → the Demolition row recomputes: actual $8,000.00 / budget $5,000.00 / remaining −$3,000.00 / red `Over budget` chip. The job-level KPI tiles do not move — only the category line gains banding because `total_budget_ex_gst` was unchanged.
- Triage the open queue item (`晶晶家 $394 水泥`, expense_id `43e66442-…`):
  - **If admin approves it (review_status → reviewed):** all totals stay the same as above (pending was already counted).
  - **If admin rejects it:** Concrete category drops out of the category list; job KPI tiles fall to `Spent inc GST = $8,950.00`, `Spent ex GST = $8,950.00`, `gst_amount = $0`, `Remaining ex GST = $111,050.00`, `% consumed = 7.46%`.
- Language toggle EN ↔ ZH on both pages — all new strings flip; English labels (`Spent inc GST` etc.) round-trip cleanly.

### Regression

Full `pytest` stays green at end of each batch. No Phase 1 / Phase 2 tests should change. Admin `npx tsc --noEmit` and `npm run build` remain clean. Mobile `npm run typecheck` + `npm run export:web` remain green (they were the Batch 4c gate; Phase 3 Lite must not regress them either).

---

## Batches (3)

### Batch 1 — Backend aggregation + tests

1. **T-A: Service module.** New file `backend/app/services/budget_summary.py` with `summarize_job(db, job_id) -> JobBudgetSummary` and `summarize_jobs(db) -> dict[UUID, JobSummary]` (the latter used by extended `GET /jobs`). Pure aggregation with the rules in the Data model section.
2. **T-B: Schemas.** New `backend/app/schemas/budget_summary.py` with `JobSummary`, `CategoryBudgetRow`, `JobBudgetSummary`. Existing `JobPublic` schema gains a nullable `summary: JobSummary | None` field.
3. **T-C: Endpoint.** New `GET /jobs/{job_id}/budget-summary` route in `backend/app/api/jobs.py`. Existing `GET /jobs` extended to include `summary` (single trip — service computes summaries in batch).
4. **T-D: Backend tests.** Both unit and integration test files above. All edge cases.

**Batch-1 exit:** `uv run pytest -v` green including new tests; OpenAPI spec regen byte-identical to manual inspection.

### Batch 2 — Admin web wiring

5. **T-E: TS types regen.** `pwsh scripts/gen-types.ps1` — both `mobile/src/api/types.ts` and `admin/src/api/types.ts` updated. Mobile types end up with the new endpoint shape but mobile doesn't consume it (Expo is preserved per Batch 4c, no UI work there).
6. **T-F: Hook.** New `admin/src/api/hooks/useBudgetSummary.ts` with `useJobBudgetSummary(jobId)`. `useJobs` already hits the same endpoint that's been extended; its return type just gains the new `summary` field automatically via TS.
7. **T-G: Jobs list UI.** Update `admin/src/pages/Jobs.tsx` to render the new columns, status chip, and % consumed sort. Add the i18n keys.
8. **T-H: Job detail UI.** Update `admin/src/pages/JobDetail.tsx` with the KPI header row + Budget-vs-actual panel. Add the remaining i18n keys.

**Batch-2 exit:** `cd admin && npx tsc --noEmit` clean; `npm run build` clean; both pages render against the running backend.

### Batch 3 — Manual E2E + regression gate + commit

9. **T-I: Manual E2E via Claude Preview.** Walk the bullets in the Frontend section above. Verify against the live trial DB — 5 real 晶晶 expenses are already there, so KPI numbers should be non-zero immediately.
10. **T-J: Regression gate.** Full `pytest`, admin `npx tsc --noEmit`, admin `npm run build`, mobile `npm run typecheck`, mobile `npm run export:web`. All green.
11. **T-K: Commit + report.** One commit with the new files + the modified pages, message records the Branch B recommendation as an operator decision (not an auto-trigger; framework's mandatory phrase wasn't formally cleared because no `visibility-gap` count was tallied), and links back to this plan.

**Batch-3 exit:** Phase 3 Lite ships. Builder can answer "am I over budget on 晶晶?" by looking at one screen.

---

## Verification at end of each batch

- **After Batch 1:** new tests green; full suite green; OpenAPI shows the new endpoint; sample request via curl returns the expected shape.
- **After Batch 2:** admin app builds cleanly; both pages load with the new fields populated against the live backend.
- **After Batch 3:** manual E2E confirms the 5 trial 晶晶 entries surface correct totals; language toggle works on both pages; full regression gate passes.

---

## Critical files

### New this phase

**Backend:**
- `backend/app/services/budget_summary.py`
- `backend/app/schemas/budget_summary.py`
- `backend/tests/services/test_budget_summary.py`
- `backend/tests/api/test_jobs_summary_api.py`

**Frontend:**
- `admin/src/api/hooks/useBudgetSummary.ts`

### Modified

**Backend:**
- `backend/app/api/jobs.py` — adds the new `/budget-summary` route, extends `GET /jobs` response
- `backend/app/schemas/job.py` — adds optional `summary` field on `JobPublic`

**Frontend:**
- `admin/src/pages/Jobs.tsx` — new columns + sort + chip
- `admin/src/pages/JobDetail.tsx` — KPI header + Budget-vs-actual panel
- `admin/src/i18n/{en,zh}.json` — ~16 new keys (Spent inc GST · Spent ex GST · Budget ex GST · Remaining ex GST · Actual ex GST · % consumed · On track · Approaching · Over budget · No budget · No budget set · Budget vs actual · Status · Category · No expenses on this job yet · No category budgets set)
- `admin/src/api/types.ts` — regenerated
- `mobile/src/api/types.ts` — regenerated (no Expo UI consumes it; Phase 4c preservation)

### Not modified

- Phase 1 models, migrations, enums
- Phase 2 models, migrations, enums, parser pipeline
- `backend/app/database.py`, `deps.py`, `core/`
- Mobile app source (Expo preservation)
- Existing admin pages other than the two listed

---

## Out of scope (deferred elsewhere)

These are **explicitly excluded** from Phase 3 Lite and ship in a separate phase if and when justified:

- **Phase 2.5 — Claude LLM fallback** — the rules-only parser handled the trial fine; revisit if a future trial logs `parser-miss ≥ 5`.
- **Review queue expansion** — add-alias-from-review, resolve-panel extensions, description polish. Trial logged 0 review-friction; no signal.
- **Excel export** — Phase 4.
- **iOS / TestFlight work** — Phase 6. Expo scaffold stays compile-gated only (no new UI).
- **Attachments / receipts** — Phase 5.
- **Notifications** — never scoped; not needed for solo-builder use.
- **Labour attendance UI** — Phase 5.

These also stay deferred until **full Phase 3** (after Lite proves the visibility surface in real use):

- Top-5 suppliers / top-5 categories rollups
- Monthly trend chart
- Estimated margin (`contract_value_ex_gst − actual_ex_gst`)
- Reviewed-vs-pending banding on the totals
- Drill-down from a KPI to the feeding expense list
- Server-side ordering for the % consumed sort (move from client-side once the job count grows)

---

## Why this plan stays small

The operator decision after the trial was clear: capture is fine, visibility is the next gap to close. The framework was designed to surface that distinction even though the formal `visibility-gap ≥ 3` trigger wasn't independently tallied (the trial used an off-system log). Phase 3 Lite is the surgical surface that closes the visibility gap and nothing more. Every line of code in this plan reads from data Phase 1 / Phase 2 already collect. No schema migration. No new conceptual model. No second client to keep in sync. The build is small because the recommendation is small.
