# Phase 3 Lite+ — Budget clarity + target margin settings

> **Direction:** small follow-up iteration on top of Phase 3 Lite, **not** Phase 4. Driven by operational feedback (2026-05-10) after the live `daefdeef-…` 晶晶 baseline was adopted: the existing `/jobs/:id` header repeats budget figures in two competing styles, the budget input is ex-GST only (forces the user to mentally divide by 1.1 when working off a contract that quotes inc-GST), and there is no surface for target profit margin even though `contract_value_ex_gst` is already stored. This plan addresses exactly those three gaps. Anything beyond them (full Phase 4 Excel export, attachments, iOS, labour) stays out of scope and ships separately.
>
> **Operator-decided scope (2026-05-10, revised post-review):**
>
> * **A — GST basis:** A2-lite. Storage stays ex-GST canonical (no duplicate inc-GST columns). UI accepts inc OR ex on input and converts to ex before submit. Conversion centralised in one TS helper with thorough rounding documentation. Display shows budget figures with explicit GST basis; spent tiles already show both bases per Phase 3 Lite.
> * **B — Profit ratio:** B3-lite, with **explicit non-misleading wording**. Canonical concepts are `target_profit_ratio_pct`, `target_cost_limit_ex_gst`, `budgeted_profit_ex_gst`, `budgeted_profit_ratio_pct`, `budget_delta_vs_target_cost_ex_gst`, and the existing Phase 3 Lite `percent_consumed` / `remaining_ex_gst` pair (collectively "cost-to-date vs budget"). The mid-project value `contract_value_ex_gst − cost_to_date_ex_gst` is **not** called "actual profit" — it is shown only as a contextual secondary line labelled **"Remaining contract value after costs to date"** with an explicit "not actual profit" disclaimer, because future costs are not included. **No hard lock** on inputs — the user can override total budget freely. Guided calculator, not an accounting constraint.
> * **C — Personalization (narrow):** GST display toggle (UI-only via localStorage), per-job warning thresholds (amber/red), per-job target profit %. Stored thresholds remain nullable; effective thresholds are exposed as separate `effective_warning_amber_pct` / `effective_warning_red_pct` fields on the API summary so stored values are never overloaded with fallback defaults. Nothing else.

**Goal:** Make budget intent explicit and easy to enter from the contract the builder is actually holding, and surface "what is my budgeted target margin and how does cost-to-date track against the budget?" alongside Phase 3 Lite's "am I over budget?". Existing Phase 3 Lite numbers continue to render unchanged for jobs with no target profit set.

**Architecture:** Additive on Phase 1 + Phase 2 + Phase 3 Lite. **One** Alembic migration adds three nullable columns to `jobs` plus four CHECK constraints. Service-layer extension on `budget_summary` adds derived margin fields and effective-threshold computation. Two existing admin pages get richer header / form display; no new routes, no new top-level nav.

**Tech stack additions:** none.

---

## Reuse from earlier phases

| Earlier artefact | Path | Reused for |
|---|---|---|
| `jobs.contract_value_ex_gst` | `backend/app/models/job.py` (Phase 1) | Margin math input |
| `jobs.total_budget_ex_gst` | `backend/app/models/job.py` (Phase 1) | Target-cost-limit delta + budgeted profit |
| `expenses.amount_ex_gst` aggregation | `backend/app/services/budget_summary.py` (Phase 3 Lite) | Cost-to-date snapshot |
| `JobPublic` / `JobUpdate` / `JobCreate` | `backend/app/schemas/job.py` | Extended with new stored fields |
| `JobSummary` / `JobBudgetSummary` | `backend/app/schemas/budget_summary.py` (Phase 3 Lite) | Extended with margin + effective-threshold fields |
| `getBudgetBand` / `BudgetChip` | `admin/src/lib/budget.tsx` (Phase 3 Lite) | Now accepts effective amber/red thresholds and resolves to a 5-band scheme (see Chip semantics) |
| TanStack Query hooks pattern | `admin/src/api/hooks/{useJobs,useBudgetSummary}.ts` | New `useGstDisplayPref` localStorage hook |
| OpenAPI types regen | `pwsh scripts/gen-types.ps1` | Run once after Batch 1 lands |

---

## Data model

**One Alembic migration.** Three new nullable columns on `jobs`, plus four CHECK constraints.

| Column | Type | Nullable | Default | Purpose |
|---|---|---|---|---|
| `target_profit_ratio_pct` | `Numeric(5, 2)` | yes | NULL | Target profit margin as a percent (e.g. `15.00` = 15%). NULL = not set. |
| `warning_amber_pct` | `Numeric(5, 2)` | yes | NULL | Per-job amber threshold for the budget chip. NULL = use system default (resolved at the API boundary as `effective_warning_amber_pct = 80.00`). |
| `warning_red_pct` | `Numeric(5, 2)` | yes | NULL | Per-job red threshold for the budget chip. NULL = use system default (resolved at the API boundary as `effective_warning_red_pct = 100.00`). |

**Stored values are never overwritten with defaults.** A NULL stays NULL in the DB. The 80 / 100 fallbacks live in a single service-layer helper (`_effective_thresholds`) and surface only via the new `effective_*` API fields. This is what point 3 of the operator review required.

**CHECK constraints (added in the same migration):**

| Constraint name | SQL |
|---|---|
| `ck_jobs_target_profit_ratio_pct_range` | `target_profit_ratio_pct IS NULL OR (target_profit_ratio_pct >= 0 AND target_profit_ratio_pct < 100)` |
| `ck_jobs_warning_amber_pct_nonneg` | `warning_amber_pct IS NULL OR warning_amber_pct >= 0` |
| `ck_jobs_warning_red_pct_positive` | `warning_red_pct IS NULL OR warning_red_pct > 0` |
| `ck_jobs_warning_amber_lt_red` | `warning_amber_pct IS NULL OR warning_red_pct IS NULL OR warning_amber_pct < warning_red_pct` |

Pydantic also enforces these (defense in depth, better error messages, surfaces the violation as `422 Unprocessable Entity` instead of letting it reach the DB layer). The CHECK constraints are the backstop for callers that bypass Pydantic (admin SQL scripts, future API clients with stale validation, etc.).

**Why percent (not fraction).** Phase 3 Lite's `percent_consumed` is already a percent (string `"7.76"`). Storing target / thresholds as the same percent unit keeps the entire margin/threshold/consumption surface in one consistent unit, avoiding ×100 conversions in the service.

**Why per-job thresholds.** A "tight-margin renovation" might want amber at 70%; a "scoped-out new build" might tolerate 90%. Per-job is the smallest data model that captures real heterogeneity without building a settings page or a user-pref table. NULL fallback to system defaults (via `effective_*` API fields) means existing jobs are unaffected and the UI degrades gracefully.

**Why no inc-GST columns.** Per A2-lite. Storing inc-GST budget would either:
* Duplicate the canonical ex-GST and risk drift, or
* Require a `budget_basis` flag and conditional reads everywhere

Neither is justified by the operational need (which is **input ergonomics**, not storage shape). The UI does the ÷1.1 at submit time and the rest of the system stays single-canonical.

**No category-budget schema change.** `job_category_budgets.budget_amount_ex_gst` continues to store ex-GST; the category-budget add form gets the same GST input toggle so users can enter inc and have it converted.

---

## API surface

**No new endpoints.** The existing routes carry the new fields:

* `POST /jobs` — `JobCreate` body accepts the three new optional fields (subject to Pydantic + CHECK constraint enforcement)
* `PATCH /jobs/{id}` — `JobUpdate` body accepts the three new optional fields (same enforcement)
* `GET /jobs` — `JobPublic` rows carry the three new **stored** fields, plus the embedded `summary: JobSummary` carries the two new `effective_*` threshold fields
* `GET /jobs/{id}` — `JobWithDetailPublic` carries the stored fields via `JobPublic` inheritance
* `GET /jobs/{id}/budget-summary` — `JobBudgetSummary` extended with five new derived fields plus the two `effective_*` threshold fields

### `JobPublic` extension

```jsonc
{
  // ... existing Phase 1 fields unchanged ...

  "target_profit_ratio_pct": "15.00",      // STORED, may be NULL
  "warning_amber_pct": null,               // STORED, may be NULL — NULL means "use system default"
  "warning_red_pct": null,                 // STORED, may be NULL — NULL means "use system default"

  "summary": {
    // ... existing Phase 3 Lite JobSummary fields unchanged ...

    "effective_warning_amber_pct": "80.00", // ALWAYS populated (stored override OR 80.00 fallback)
    "effective_warning_red_pct": "100.00"   // ALWAYS populated (stored override OR 100.00 fallback)
  }
}
```

### `JobBudgetSummary` extension

```jsonc
{
  // ... existing Phase 3 Lite fields unchanged ...

  // -- target margin --
  "target_profit_ratio_pct": "15.00",                   // passthrough from job (may be NULL)
  "target_cost_limit_ex_gst": "170000.00",              // contract * (1 - target/100); NULL if either input missing
  "budgeted_profit_ex_gst": "12000.00",                 // contract - total_budget; NULL if either input missing
  "budgeted_profit_ratio_pct": "6.00",                  // budgeted_profit / contract * 100; NULL if contract NULL or 0, or budget NULL
  "budget_delta_vs_target_cost_ex_gst": "18000.00",     // total_budget - target_cost_limit; NULL if any of contract/target/total_budget NULL. Positive => budget exceeds target cost limit => lower margin than target. Negative => budget below target cost limit => more conservative than target.

  // -- effective thresholds (always populated) --
  "effective_warning_amber_pct": "80.00",
  "effective_warning_red_pct": "100.00"
}
```

### What is intentionally NOT a backend field

* **"Actual profit"** in any form. Mid-project this would be misleading — `contract − cost_to_date` ignores future costs, so it is not actual profit. The frontend may compute and display `contract − actual_ex_gst` as a **secondary contextual line** labelled exactly **"Remaining contract value after costs to date"** with a small **"Not actual profit — does not include future costs"** disclaimer underneath. It does not get a backend field, a KPI tile, or a margin-style highlight.
* **`cost_to_date_vs_budget`** as a new field. This is the canonical phrasing for the existing Phase 3 Lite `percent_consumed` (cost-to-date as % of budget) + `remaining_ex_gst` (budget − cost-to-date) pair. Phase 3 Lite+ uses this phrasing in UI labels and docs to anchor the concept clearly, but does not introduce a duplicate field.

### Nullable-rules reference

| Field | Required inputs | If any input missing |
|---|---|---|
| `target_profit_ratio_pct` (passthrough) | (passthrough only) | NULL |
| `target_cost_limit_ex_gst` | contract, target | NULL |
| `budgeted_profit_ex_gst` | contract, total_budget | NULL |
| `budgeted_profit_ratio_pct` | contract (and contract > 0), total_budget | NULL |
| `budget_delta_vs_target_cost_ex_gst` | contract, target, total_budget | NULL |
| `effective_warning_amber_pct` | (always populated — stored OR 80.00 fallback) | n/a |
| `effective_warning_red_pct` | (always populated — stored OR 100.00 fallback) | n/a |

---

## UI

### GST input toggle (forms)

Applies to three money inputs across two forms:

| Form | Inputs |
|---|---|
| New Job modal (`Jobs.tsx`) | contract value, total budget |
| Job Settings form (new on `JobDetail.tsx`) | contract value, total budget |
| Add Category Budget form (`JobDetail.tsx`) | budget amount |

Each money input gets a small adjacent two-state toggle: `[ex] [inc]`. Default `ex` (matches today's input semantics so existing flows are unchanged). When `inc` is selected, the UI:

* Submits `value / 1.1` rounded to 0.01 to the existing ex-GST API field
* Shows a small live "= $X.XX ex GST" hint under the input so the user can sanity-check the conversion before submitting

Conversion logic lives in **one TS helper**, `admin/src/lib/gst.ts` (see GST basis rules + Test strategy below). No backend change for the toggle.

### GST display preference (localStorage)

Single per-browser preference, three modes:

| Mode | What it does |
|---|---|
| `ex` (default) | Budget tiles show only the ex-GST primary value (today's behaviour) |
| `both` | Budget tiles show ex-GST primary + small secondary line "= $X inc GST" |
| `inc` | Budget tiles show inc-GST as primary, ex-GST as small secondary |

Stored as `localStorage['sitetracker-admin-gst-display']`. Selector in the AppShell header next to the existing language toggle. Spent tiles already show both bases via Phase 3 Lite's separate columns; this preference does not touch them.

### Job Detail page (`JobDetail.tsx`)

Four changes, all additive:

1. **Top dl cleanup (Phase 1 leftover).** Format `total_budget` and `contract_value` with `formatMoney` so they render `$188,000.00` not `188000.00`. If GST display pref is `both` or `inc`, append the inc-basis figure.

2. **Cost-to-date snapshot (small contextual line).** Below the formatted contract value in the top dl, when `contract_value_ex_gst` is set, show:
   * Primary: **"Remaining contract value after costs to date: $X.XX"** where `X.XX = contract − actual_ex_gst` (UI-derived from existing Phase 3 Lite summary fields)
   * Secondary, in smaller muted text: **"Not actual profit — does not include future costs"**

   This is **not a KPI tile** and is **not framed as profit**. It is a contextual hint near the contract value so the user can see "headroom remaining in the contract before zero margin", with the explicit disclaimer baked in.

3. **New KPI tile (one) in the existing KPI header row:** **`Target profit %`** — primary `15.00%`, hidden entirely when `target_profit_ratio_pct` is NULL. The `Budgeted profit ex GST` figure does not get its own KPI tile (it lives in the Target margin panel below); this avoids bloating the KPI header with too many primary numbers.

4. **New "Target margin" panel** below the existing Phase 3 Lite Budget-vs-actual panel, only rendered when `target_profit_ratio_pct` is set. Three rows:
   * **Target cost limit ex GST** — `target_cost_limit_ex_gst` from the API; secondary line shows the formula "= Contract × (1 − target%)"
   * **Your budget ex GST** — `total_budget_ex_gst`; secondary shows the delta "+$18,000.00 over target cost limit (lower margin than target)" or "−$5,000.00 below target cost limit (more conservative than target)"
   * **Budgeted profit ex GST** — `budgeted_profit_ex_gst`; secondary shows the budgeted margin ratio "= 6.00% margin given chosen budget" using `budgeted_profit_ratio_pct`

   When the user has set a target but neither contract nor budget is set, the panel shows a single hint: **"Set contract value to compute target cost limit"**.

### Job Settings form (new on `JobDetail.tsx`)

Existing detail page has alias + category-budget forms but **no edit form for the job's own scalar fields** (contract, budget, status, etc.) — those are only set at creation. Phase 3 Lite+ adds a small inline "Job settings" form so the user can:

* Edit contract value (with GST toggle)
* Edit total budget (with GST toggle)
* Edit target profit % (plain percent input, range `0` to `99.99`)
* Edit warning amber / red thresholds (two plain percent inputs, with placeholders showing the system defaults `80` / `100`; client-side validation enforces `0 ≤ amber < red`, `red > 0`)

Submit hits the existing `PATCH /jobs/{id}` (extended in Batch 1).

### Jobs list (`Jobs.tsx`)

Two changes:

1. **Per-job effective thresholds applied to chip.** `getBudgetBand` is rewritten to take the effective thresholds and return one of the **five** bands described in the Chip semantics section below. Each row uses its own `effective_warning_amber_pct` / `effective_warning_red_pct` from the embedded `summary`.
2. **Optional inc-GST display** for `Budget ex GST` column when GST display pref is `both` or `inc` (small secondary line under the cell).

No new column for target profit on the list — keeps the table from getting wider; target profit is a detail-page concern.

---

## Chip semantics (revised — frozen)

Five bands, replacing the four in Phase 3 Lite:

| Band code | Trigger | Default label (en / zh) | Style |
|---|---|---|---|
| `on_track` | `percent_consumed < amber` | `On track` / `正常` | green |
| `approaching` | `amber ≤ percent_consumed < red` | `Approaching` / `接近预算` | amber |
| `critical` | `red ≤ percent_consumed < 100` *(only fires when red < 100)* | `Critical` / `危急` | orange |
| `over_budget` | `percent_consumed ≥ 100` **OR** `remaining_ex_gst < 0` | `Over budget` / `超预算` | red |
| `no_budget` | `total_budget_ex_gst` is NULL or 0 | `No budget` / `无预算` | neutral |

**Rules (frozen by point 2 of the operator review):**

* **`Over budget` only fires when `percent_consumed ≥ 100` OR `remaining_ex_gst < 0`.** Custom red thresholds below 100% must NOT trigger the `over_budget` band — that wording would falsely imply the budget has been exceeded when it has not.
* **`critical` is the new band for "you crossed your custom red warning but you are still under your budget"** — it only exists when the user explicitly set a red threshold below 100. With the default red of 100, the `critical` band collapses to empty (since `red ≤ % < 100` is empty when `red = 100`) and behaviour matches Phase 3 Lite exactly.
* **The default label for `critical` is "Critical" / "危急"** based on the operator-suggested wording set ("Critical" / "High risk" / "Near limit"). This is an i18n key — the label can be retuned without code changes.
* **Tie-break order when multiple conditions match** (e.g. % is over both red and 100): the higher-severity band wins, and `over_budget` is the highest. Order: `over_budget > critical > approaching > on_track`.

The Phase 3 Lite admin UI's `BudgetChip` accepts the band code and renders the appropriate Tailwind class + i18n label; the rewrite extends this from 4 to 5 bands without changing the component's contract.

---

## Formulas

All computed in `backend/app/services/budget_summary.py`. Decimal throughout, quantize to 0.01.

```
# Inputs (all may be NULL):
#   contract = jobs.contract_value_ex_gst
#   target   = jobs.target_profit_ratio_pct  (percent, e.g. 15.00)
#   budget   = jobs.total_budget_ex_gst
#   actual   = SUM(amount_ex_gst) over (reviewed, pending) expenses

# GST conversion (UI-only; lives in admin/src/lib/gst.ts):
#   inc_from_ex(ex)  = round(ex * 1.1, 2)
#   ex_from_inc(inc) = round(inc / 1.1, 2)

# Target cost limit (when contract + target both set):
target_cost_limit_ex_gst = (contract * (Decimal("100") - target) / Decimal("100")).quantize(0.01)

# Budgeted profit (when contract + budget both set):
budgeted_profit_ex_gst = (contract - budget).quantize(0.01)

# Budgeted profit ratio (when contract set AND contract > 0, AND budget set):
budgeted_profit_ratio_pct = (budgeted_profit_ex_gst / contract * Decimal("100")).quantize(0.01)

# Delta vs target cost (when contract + target + budget all set):
budget_delta_vs_target_cost_ex_gst = (budget - target_cost_limit).quantize(0.01)
# positive => budget exceeds target cost limit => lower margin than target
# negative => budget is below target cost limit => more conservative than target

# Effective thresholds (always populated):
effective_warning_amber_pct = warning_amber_pct ?? Decimal("80.00")
effective_warning_red_pct   = warning_red_pct   ?? Decimal("100.00")
```

**Single-source helper for thresholds.** A small `_effective_thresholds(stored_amber, stored_red) -> (Decimal, Decimal)` function in `services/budget_summary.py` is the single source of the 80 / 100 default. Anywhere else that needs the effective thresholds (jobs list service path, detail summary service path, future surfaces) calls this helper.

**No actual-profit formulas.** Deliberately omitted — see the disclaimer in the API surface section.

---

## GST basis rules (frozen)

1. **Storage:** ex-GST only. Never store inc-GST budget figures.
2. **Conversion factor:** AU 10% GST. `inc = ex × 1.1`, `ex = inc ÷ 1.1`. This is an aggregate-planning conversion and is **not** subject to the cash-payment GST rule (which only applies per expense row in Phase 2 — budgets and contracts have no payment method).
3. **Centralised in one helper.** `admin/src/lib/gst.ts` exports `incToEx(inc: string): string`, `exToInc(ex: string): string`. Both take and return Decimal-string values to avoid float drift on the ÷1.1 / ×1.1 path. Internal computation uses native JS number arithmetic with explicit `.toFixed(2)` quantization, which is safe for amounts up to ~$10 million given JS's 15-16 sig-fig precision; this assumption is documented in the helper's JSDoc with worked examples and a tolerance note for round-tripping.
4. **Quantize:** every conversion result quantizes to two decimal places. UI displays preserve trailing zeros via `formatMoney`.
5. **Default basis:** all forms default to `ex` so that pasting an existing ex-GST contract value into the form is a no-op.
6. **Display labels** (frozen by the plan, never abbreviated): `inc GST`, `ex GST`. Phase 3 Lite's existing labels (`Spent inc GST` etc.) are unchanged.
7. **Round-trip is not exact.** `exToInc(incToEx(x))` may differ from `x` by up to 1 cent because each step quantizes independently. The helper's JSDoc warns explicitly; the UI does not chain conversions during a single user action.

---

## Target profit calculation rules (frozen)

1. **Target is per-job**, not global.
2. **Range:** `0 ≤ target_profit_ratio_pct < 100` enforced by **both** Pydantic (422 to caller) and a DB CHECK constraint (backstop). A 100% target would imply zero budget and is meaningless; negative would be a loss target.
3. **Target cost limit is informational, not enforced.** The user can save any `total_budget_ex_gst` regardless of whether it matches the target cost limit.
4. **Target cost limit uses contract value at face.** If contract is later updated, the target cost limit recomputes on next read; we do not snapshot it.
5. **Budgeted profit is computed against the user's chosen budget**, not against the target cost limit. This is the actually-planned profit given the budget on file.
6. **Cost-to-date snapshot is not actual profit.** The frontend may surface `contract − actual_ex_gst` as a secondary contextual line labelled exactly "Remaining contract value after costs to date" with a "not actual profit" disclaimer. There is no backend field for it and no profit framing anywhere in the UI.
7. **Negative `budgeted_profit_ex_gst` is allowed** (jobs where the chosen budget exceeds the contract value). UI styles the row red but does not block submission — the chip / banding logic on cost-to-date vs budget remains the source of truth for "are you over budget" wording.

---

## Test strategy

### Backend unit tests (extend `tests/test_budget_summary_service.py`)

* `target_profit_ratio_pct` passthrough — set on job, appears in summary
* Target cost limit math — contract 200,000 + target 15.00 → target_cost_limit 170,000.00
* Target cost limit NULL when contract is NULL
* Target cost limit NULL when target is NULL
* Budgeted profit math — contract 200,000 + budget 188,000 → budgeted_profit 12,000.00
* Budgeted profit NULL when either input missing
* Budgeted profit ratio — same → 6.00 (quantized)
* Budgeted profit ratio NULL when contract is 0 (avoid divide-by-zero)
* Budgeted profit allowed negative — contract 100,000 + budget 120,000 → budgeted_profit −20,000.00, ratio −20.00
* Delta vs target cost — contract 200,000, target 15, budget 188,000 → budget − target_cost_limit = 188,000 − 170,000 = 18,000.00 (budget exceeds target cost limit)
* Delta vs target cost negative — contract 200,000, target 15, budget 160,000 → 160,000 − 170,000 = −10,000.00 (more conservative than target)
* Effective threshold passthrough — per-job override returns the override
* Effective threshold fallback — NULL stored returns 80.00 / 100.00
* Effective threshold mixed — only amber set, red NULL → effective amber = stored, effective red = 100.00
* Stored thresholds remain NULL after summary computation — never overwritten

### Backend chip-band logic tests (new — `tests/test_budget_summary_service.py` or a peer file)

The chip computation lives on the frontend per Phase 3 Lite, but the **band-routing rules are part of the operator-frozen contract** and are exercised here as pure-function tests of a `compute_band(percent, remaining, total_budget, eff_amber, eff_red)` helper that mirrors the frontend logic. (The frontend imports the same logic shape from generated TS; this backend test exists to guard the contract.) Cases:

* `on_track` — % below amber
* `approaching` — % at or above amber, below red
* `critical` — red < 100 and % at or above red and below 100
* `critical` does NOT fire when red is 100 (collapses to empty band)
* `over_budget` — % >= 100
* `over_budget` — remaining_ex_gst < 0 even if % < 100 (e.g. budget = 0 with rounding edge)
* `over_budget` wins over `critical` when both would match
* `no_budget` — total_budget NULL or 0
* `over_budget` label is NOT used for custom red below 100 — verify the label key is `critical` not `over_budget` for those cases

### Backend CHECK constraint tests (new — `tests/test_budget_summary_service.py`)

These bypass Pydantic to verify the DB constraints fire as a backstop. Each uses raw SQL through the session to attempt the violating insert/update and expects `IntegrityError`:

* `target_profit_ratio_pct = 100.00` — violates range CHECK
* `target_profit_ratio_pct = -1.00` — violates range CHECK
* `warning_amber_pct = -0.01` — violates non-neg CHECK
* `warning_red_pct = 0.00` — violates positive CHECK
* `warning_amber_pct = 90.00, warning_red_pct = 80.00` — violates amber-lt-red CHECK
* `warning_amber_pct = 80.00, warning_red_pct = 80.00` — violates amber-lt-red (strict <)
* All of `target_profit_ratio_pct = 99.99`, `warning_amber_pct = 0`, `warning_red_pct = 0.01`, `amber=NULL/red=80`, `amber=80/red=NULL`, `amber=NULL/red=NULL` — succeed (boundary inclusivity verified)

### Backend integration tests (extend `tests/test_jobs_summary_api.py`)

* `POST /jobs` with new fields → persisted + returned with stored fields
* `POST /jobs` with `target_profit_ratio_pct = 100.00` → 422 from Pydantic (does not reach DB)
* `POST /jobs` with `warning_amber_pct = 90, warning_red_pct = 80` → 422 from Pydantic
* `PATCH /jobs/{id}` with new fields → updated + returned
* `GET /jobs` carries `effective_warning_amber_pct` / `effective_warning_red_pct` on every row's `summary` (with 80 / 100 fallback for un-set jobs); also carries the stored fields (NULL or set) on the row itself
* `GET /jobs/{id}/budget-summary` returns the new derived fields + effective thresholds with correct math
* Stored thresholds round-trip — set per-job override, verify `warning_amber_pct` field on `JobPublic` shows the stored value (not the effective fallback)

### Frontend tests

Manual E2E via Claude Preview (existing pattern):

* On the live `daefdeef-…` 晶晶 (no contract value, no target set today):
  - Open new "Job settings" form, set contract value to `$200,000` (ex), target profit `15`, leave budget at `$188,000`. Verify:
    - Target cost limit panel: `$170,000.00 (= Contract × (1 − 15%))`
    - Your budget: `$188,000.00 (+$18,000.00 over target cost limit, lower margin than target)`
    - Budgeted profit: `$12,000.00 (= 6.00% margin given chosen budget)`
    - Cost-to-date snapshot near contract value: "Remaining contract value after costs to date: $42,820.00 — Not actual profit, does not include future costs"
  - Switch GST input toggle to `inc`, enter `$220,000` for contract → submits ex `$200,000.00`, shows live conversion hint `= $200,000.00 ex GST`
  - Switch GST display preference to `both` → all budget tiles gain the inc-GST secondary line (`$188,000.00 ex GST  =  $206,800.00 inc GST`)
  - Switch back to `ex` → secondary lines disappear
* **Chip semantics (revised — replaces the misleading example from the previous plan revision):**
  - With default thresholds (NULL stored → effective 80 / 100), 晶晶 at 83.61% shows `Approaching` (amber)
  - Set per-job amber threshold to `60`, red to `90` → 晶晶's chip flips to **`Critical`** (orange). It must NOT show "Over budget" — 83.61% < 100 and `remaining_ex_gst > 0`, so the budget has not actually been exceeded.
  - Reset thresholds to NULL → chip returns to `Approaching`
  - Demo a true `Over budget`: temporarily set target budget to a value lower than current spend (`$100,000` ex on 晶晶 with $157,180 ex spent) → chip shows **`Over budget`** (red) because `percent_consumed > 100` AND `remaining_ex_gst < 0`. Reset budget after demo.
* GST helper rounding (manual E2E covers the rounding contract documented in `gst.ts`):
  - Input inc `100` → ex `90.91` (90.9090… rounds to 90.91)
  - Input inc `0.01` → ex `0.01` (0.00909… rounds to 0.01)
  - Input ex `90.91` → inc `100.00` (90.91 × 1.1 = 100.001 → 100.00 — note the 1-cent round-trip warning)
  - Input inc `1100` → ex `1000.00` (exact)
* Language toggle EN ↔ ZH on both pages — all new strings flip cleanly, including the new `Critical` / `危急` chip label

### Regression

Full `pytest` stays green at end of each batch (currently 467/467; this plan adds ~28 new tests — ~16 service + chip-band + ~6 CHECK + ~6 API = target ~495). Admin `npx tsc --noEmit` and `npm run build` remain clean. Mobile `npm run typecheck` + `npm run export:web` remain green.

A vitest setup for unit-testing the `gst.ts` helper is **out of scope** for Phase 3 Lite+; the rounding contract is verified via thorough JSDoc examples + manual E2E. Vitest can come later as its own small infra iteration if more JS helpers accrue.

---

## Batches (3)

### Batch 1 — Backend: schema + migration + service + API + tests

1. **T-A: Migration.** New Alembic revision adding `target_profit_ratio_pct`, `warning_amber_pct`, `warning_red_pct` to `jobs`, plus the four CHECK constraints. All columns nullable, no DB-level defaults.
2. **T-B: Model.** Update `app/models/job.py` with the three columns and the `__table_args__` CHECK constraints (so SQLAlchemy metadata mirrors the migration for the test DB bootstrap which uses `Base.metadata.create_all` not Alembic).
3. **T-C: Schemas.** Extend `JobCreate`, `JobUpdate`, `JobPublic` with the three stored fields (with Pydantic range validators). Extend `JobSummary` with `effective_warning_amber_pct` / `effective_warning_red_pct`. Extend `JobBudgetSummary` with the five new derived margin fields plus the two `effective_*` fields.
4. **T-D: Service.** Extend `summarize_job` and `summarize_jobs` in `services/budget_summary.py` to compute the new derived fields. Add `_effective_thresholds(stored_amber, stored_red) -> (Decimal, Decimal)` as the single source of the 80 / 100 default. Add `compute_band(percent, remaining, total_budget, eff_amber, eff_red) -> Band` for the chip-routing contract guard.
5. **T-E: API.** No new routes. `POST /jobs`, `PATCH /jobs/{id}` accept the new fields via the extended schemas; `GET /jobs` and `GET /jobs/{id}/budget-summary` return them.
6. **T-F: Tests.** Service unit tests + chip-band tests + CHECK constraint tests + API integration tests per the test strategy.

**Batch-1 exit:** `pytest -v` green including all new tests; OpenAPI spec includes the new fields; sample curl returns the expected shape with computed values; CHECK constraints verified to fire when Pydantic is bypassed.

### Batch 2 — Admin: forms + GST input toggle + Job Settings form

7. **T-G: Type regen.** Run the OpenAPI → TS regen against the running backend on `:8002`. Both `admin/src/api/types.ts` and `mobile/src/api/types.ts` updated.
8. **T-H: GST helpers.** `admin/src/lib/gst.ts` with `incToEx`, `exToInc`, thorough JSDoc covering rounding (zero, exact, repeating-decimal cases, round-trip tolerance, large values). Plus `<GstAmountInput>` component wrapping a money input + the `[ex] [inc]` toggle + the live conversion hint.
9. **T-I: New Job modal.** Wire `<GstAmountInput>` for contract value + total budget. Default basis `ex`.
10. **T-J: Job Settings form.** New inline form on `JobDetail.tsx` (above the existing alias / category-budget sections) with the four edit field groups (contract, budget, target %, amber/red thresholds). Hits the extended `PATCH /jobs/{id}`. Client-side validation enforces `0 ≤ target < 100` and `amber < red` to mirror Pydantic + CHECK.
11. **T-K: Add Category Budget form.** Wire `<GstAmountInput>` for the budget amount.

**Batch-2 exit:** `cd admin && npx tsc --noEmit` clean; `npm run build` clean; new forms submit successfully against the running backend; conversion hints + validation errors verified manually.

### Batch 3 — Admin: display tiles + GST display pref + per-job thresholds + i18n + E2E + commit

12. **T-L: GST display pref hook.** `admin/src/api/hooks/useGstDisplayPref.ts` (TanStack Query against localStorage). Selector in `AppShell` header.
13. **T-M: Top dl cleanup + cost-to-date snapshot.** `JobDetail.tsx` — replace raw money strings with `formatMoney`; respect GST display pref. Add the "Remaining contract value after costs to date" secondary line with the "not actual profit" disclaimer beneath the contract value.
14. **T-N: Target profit % KPI tile.** Single new tile in the existing KPI header, hidden when target NULL.
15. **T-O: Target margin panel.** New panel below the existing Budget-vs-actual panel; renders only when `target_profit_ratio_pct` is set. Three rows per the UI spec.
16. **T-P: 5-band chip.** Rewrite `getBudgetBand` in `lib/budget.tsx` to take `(percent, remaining, totalBudget, effAmber, effRed)` and return one of the five bands (`on_track` / `approaching` / `critical` / `over_budget` / `no_budget`). Update `BudgetChip` to render the new band's class + i18n label. Thread `effective_*` thresholds through `Jobs.tsx` and `JobDetail.tsx`.
17. **T-Q: Optional inc-GST secondary line on `/jobs` Budget column** when GST display pref is `both` or `inc`.
18. **T-R: i18n.** ~14 new keys in `en.json` + `zh.json` covering the new labels (target_profit, target_cost_limit, budgeted_profit, budgeted_profit_ratio, budget_delta_vs_target_cost, gst_basis_inc / ex / both, warning_amber_pct, warning_red_pct, band_critical, contract_remaining_after_costs, not_actual_profit_disclaimer, etc.).
19. **T-S: Manual E2E** per the Test Strategy, against the live `daefdeef-…` 晶晶. Includes the corrected chip-semantics demo (`Critical` not `Over budget` for custom red < 100).
20. **T-T: Regression gate** (pytest, admin tsc + build, mobile tsc) and **two commits** matching the Phase 3 Lite split convention:
    * Commit 1 — backend (T-A through T-F)
    * Commit 2 — admin (T-G through T-S, including i18n + regenerated types)

**Batch-3 exit:** Phase 3 Lite+ ships. Builder can enter contract / target / budget on the form they're staring at and immediately see the target cost limit + delta + budgeted profit, with budget figures in the GST basis they prefer, with chip wording that does not falsely claim "Over budget" when the budget has not actually been exceeded.

---

## Verification at end of each batch

* **After Batch 1:** new tests green; full suite green; OpenAPI shows the new stored + derived + effective fields; sample curl: `GET /jobs/{晶晶_id}/budget-summary` shows correct derived values after `PATCH /jobs/{晶晶_id}` sets target. CHECK constraints verified by attempting raw-SQL violations.
* **After Batch 2:** admin app builds cleanly; new "Job settings" form, GST input toggle, and Add Category Budget form all submit successfully and round-trip through the backend; GST conversion hint matches the documented JSDoc examples.
* **After Batch 3:** manual E2E walks the bullets in the Frontend test strategy against the live `daefdeef-…` 晶晶, including the corrected chip-semantics demo. EN ↔ ZH round-trip clean. Full regression gate passes.

---

## Critical files

### New this phase

**Backend:**
* `backend/alembic/versions/<rev>_add_job_target_profit_and_thresholds.py`

**Frontend:**
* `admin/src/lib/gst.ts`
* `admin/src/components/GstAmountInput.tsx`
* `admin/src/api/hooks/useGstDisplayPref.ts`

### Modified

**Backend:**
* `backend/app/models/job.py` — three new columns + `__table_args__` CHECK constraints (mirrors migration for `Base.metadata.create_all` test bootstrap)
* `backend/app/schemas/job.py` — extend Create / Update / Public with stored fields + Pydantic range validation
* `backend/app/schemas/budget_summary.py` — extend `JobSummary` (effective threshold fields) + `JobBudgetSummary` (5 new derived fields + effective thresholds)
* `backend/app/services/budget_summary.py` — compute new derived fields; add `_effective_thresholds` helper; add `compute_band` helper for chip contract
* `backend/app/services/jobs.py` — accept new fields in create / update
* `backend/app/api/jobs.py` — pass new fields through to services (no route changes)
* `backend/tests/test_budget_summary_service.py` — ~16 new service + chip-band tests
* `backend/tests/test_jobs_summary_api.py` — ~6 new API tests
* New file or extension to existing: ~6 CHECK constraint tests (chosen file in Batch 1 implementation)

**Frontend:**
* `admin/src/pages/Jobs.tsx` — per-job effective thresholds in chip; optional inc-GST secondary
* `admin/src/pages/JobDetail.tsx` — Job Settings form, top dl cleanup, cost-to-date snapshot, Target profit % KPI tile, Target margin panel, per-job effective thresholds in chip
* `admin/src/components/AppShell.tsx` — GST display pref selector in header
* `admin/src/lib/budget.tsx` — `getBudgetBand` rewritten for 5-band scheme; default amber/red constants exported; new `band_critical` style
* `admin/src/i18n/en.json` + `admin/src/i18n/zh.json` — ~14 new keys
* `admin/src/api/types.ts` — regenerated
* `mobile/src/api/types.ts` — regenerated (no Expo UI consumes it; Phase 4c preservation)

### Not modified

* Phase 1 / 2 enums, expense / review queue / audit models or migrations
* Phase 2 parser pipeline
* Phase 3 Lite service math (only **extended**, no edits to existing aggregation rules)
* Mobile app source (Expo preservation)
* Other admin pages (Users, Suppliers, Expenses, Review Queue, Capture, MyExpenses)

---

## Out of scope (deferred)

* **User-level GST preference table** — localStorage covers the solo-builder case; promote to user-pref only if multi-device consistency becomes a real complaint.
* **System-wide settings page** — no settings UI in this plan; the only "settings" are per-job fields edited inline.
* **Per-category warning thresholds** — Phase 3 Lite+ uses one set of thresholds per job, applied to both the job-level chip and the per-category chips. Per-category overrides can come later if needed.
* **Snapshotted target cost limits** — recomputes on every read against the current contract value + target. No history of what the limit "was at the time".
* **Contract-value GST conversion at the API boundary** — the API accepts ex-GST only; the UI converts inc → ex before submit. We do not add an API option to accept inc-GST inputs server-side.
* **"Actual profit" surfaces** — explicitly excluded by the operator review. Mid-project profit is not knowable. The cost-to-date snapshot is the only headroom-style indicator and carries an explicit disclaimer.
* **Vitest / JS unit-test framework** — gst.ts rounding is verified via JSDoc examples + manual E2E. Vitest setup deferred to a separate iteration.
* **Customizable layout / hidden tiles / job colors / tags** — explicitly excluded by the operator narrow-scope decision.
* **Phase 4 work** — Excel export, accountant handoff. Ships separately as a distinct phase.

---

## Why this plan stays small

The operational feedback was specific and the post-review revisions tightened the wording / data contracts further:

* **Input ergonomics** → UI toggle, no DB shape change, conversion centralised in one helper
* **Margin visibility** → 1 new persisted field (target %) + derived service fields, no new endpoint, no misleading "actual profit" framing
* **Header consistency** → reuse Phase 3 Lite's `formatMoney` + new tiles in the same KPI grid
* **Chip safety** → 5-band scheme keeps "Over budget" reserved for actual budget exhaustion; "Critical" carries the user's custom warning without false claims
* **Threshold defaults** → kept entirely server-side in one helper, exposed as separate `effective_*` API fields so stored values are never overwritten

Three new columns total, four CHECK constraints, one migration, one service helper for thresholds, one helper for band routing, two new admin components, one new hook, and ~14 i18n keys. No new routes. No new top-level nav. No new conceptual model. Like Phase 3 Lite, the build is small because the request is small.
