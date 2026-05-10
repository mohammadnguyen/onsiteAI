# Phase 3 Lite++ — Auto-calculated total budget (B-auto default, both surfaces)

> **Direction:** small follow-up iteration on top of Phase 3 Lite+, **not** Phase 4. Driven by operational feedback during live use of the dashboard:
>
> > 总预算应该按照合同的总金额+利润比去自动计算 不要手动计算
> > (Total budget should be auto-calculated from contract total + profit ratio. Don't calculate manually.)
>
> **Scope correction (post-review):** the auto-budget logic must apply to **both** entry surfaces, not just the edit form. The most common path is **creating a new project** — that's where the user faces the calculator first. Limiting the change to Job Settings would still force mental math at job-creation time.
>
> **Operator-decided scope (B-auto default, both surfaces):**
>
> * **New Job modal** (`/jobs`): user enters contract value + target profit %; system auto-fills total budget by default. Manual editing requires explicitly selecting "Manual budget".
> * **Job Settings form** (`/jobs/:id`): same auto-default for new edits; existing stored data is detected on form load (auto vs override). No silent overwrite — saved budget stays as-is until the user clicks Save.
> * **When `target_profit_ratio_pct` is NULL:** total budget remains a plain manual input on both surfaces. No auto-calc possible without a target.
> * **Manual override is opt-in via radio buttons** (`Auto-calculate from target profit` / `Manual budget`).
> * **Switching manual → auto snaps the draft budget** to the calculated value (in form state, not in the DB) so the user sees what Save will commit. Save is the only commit point.
> * **Clearing contract or target returns the field to manual mode** (auto-calc is no longer possible) but **does not wipe the current budget draft** — whatever value is in the input stays as a draft until the user changes it.
> * **No silent backfill of existing jobs.** 晶晶's $188,000 stays put until the user opens Job Settings and saves changes.

**Goal:** Remove the mental ÷ / × arithmetic from the two places a builder thinks about budget — at project creation ("I won the contract for $X, I want Y% margin") and during a settings edit. The system does the math.

**Architecture:** Frontend-only iteration. **Zero backend changes** — same routes, same schemas, same DB columns, same Pydantic validators. The auto/manual choice is purely a UI affordance. The shared logic lives in a single new component `<TotalBudgetField>` consumed by both `Jobs.tsx` (modal) and `JobDetail.tsx` (settings form).

**Tech stack additions:** none.

---

## Reuse from earlier phases

| Earlier artefact | Path | Reused for |
|---|---|---|
| `JobUpdate` PATCH endpoint with explicit-null support | `backend/app/api/jobs.py` (Phase 3 Lite+ correction `d3ce2bc`) | Settings form still hits the same endpoint; auto-calc just decides what value to send |
| `JobCreate` POST endpoint | `backend/app/api/jobs.py` (Phase 1) | New Job modal still POSTs the same body shape; auto-calc decides the budget value |
| `useUpdateJob` / `useCreateJob` hooks | `admin/src/api/hooks/useJobs.ts` | Same; no change |
| `<GstAmountInput>` component | `admin/src/components/GstAmountInput.tsx` (Phase 3 Lite+ Batch 2) | Reused for the contract-value input and as the underlying input inside the new `<TotalBudgetField>` (it already supports a `disabled` prop from Batch 2 — lock state is free) |
| `useJobBudgetSummary` | `admin/src/api/hooks/useBudgetSummary.ts` | Provides `target_cost_limit_ex_gst` + `budget_delta_vs_target_cost_ex_gst` for the post-Save summary panel; the new component does its own pre-Save derivation for the live preview |
| `TargetMarginPanel` | `admin/src/pages/JobDetail.tsx` (Phase 3 Lite+ Batch 3) | Already shows target cost limit, budget delta, budgeted profit, ratio. **No change needed** — in auto mode the delta will always be 0; in override mode it shows the consequence as today |
| `incToEx` / `exToInc` / `formatMoney` / `formatPercent` | `admin/src/lib/{gst,budget}.{ts,tsx}` (Batch 2 / 3) | Used by the new component for the live conversion + display |

---

## Data model

**No schema change.** `jobs.total_budget_ex_gst` is the single source of truth for the budget — whether it was typed manually or computed from contract+target makes no semantic difference once stored. The "is this currently auto or manual?" state is derived from the stored values on form load and managed in React state thereafter.

* If `target_profit_ratio_pct` is null → form opens in **manual** mode (radios hidden, input editable, behaviour exactly like Phase 3 Lite+ today)
* If `target_profit_ratio_pct` is set AND `total_budget_ex_gst == contract × (1 − target/100)` (within ±$0.01 decimal-string tolerance) → form opens in **auto** mode (radios visible, "Auto" selected, input locked)
* If `target_profit_ratio_pct` is set AND `total_budget_ex_gst ≠ contract × (1 − target/100)` → form opens in **override (manual)** mode (radios visible, "Manual" selected, input editable, current stored value preserved, override-warning visible)

The mode is **derived once on mount**; after that, the user owns it via the radio buttons.

**No `budget_source: 'auto' | 'manual'` enum, no `is_overridden: bool` column, no DB migration.**

---

## API surface

**No new routes. No schema changes. No request/response shape changes.**

* `POST /jobs` and `PATCH /jobs/{id}` accept `total_budget_ex_gst` exactly as today. The form decides what value to send based on the user's auto-vs-override choice.
* `GET /jobs/{id}/budget-summary` already returns the three derived fields the override-warning surface needs:
  - `target_cost_limit_ex_gst` — the auto-calc target value
  - `budget_delta_vs_target_cost_ex_gst` — the override delta
  - `budgeted_profit_ratio_pct` — the override-warning ratio input

These are post-Save derivations from the backend, separate from the **pre-Save preview** the new component computes locally for the user's live feedback.

---

## UI

### New shared component: `<TotalBudgetField>`

```ts
type Mode = 'auto' | 'manual'

type Props = {
  /** Canonical ex-GST contract value as Decimal-string ("" for none). */
  contractValueExGst: string
  /** Target profit ratio as a percent string ("15.00"), "" for none. */
  targetProfitRatioPct: string
  /** Canonical ex-GST budget value as Decimal-string. */
  value: string
  /** Called with the canonical ex-GST value on every change. */
  onChange: (exGst: string) => void
  /** Initial mode on mount. Used by JobSettingsForm to honour existing stored
   *  data (auto if budget matches calc; manual if it differs or target null).
   *  When omitted, the component picks 'auto' if both contract+target set,
   *  'manual' otherwise — matches the New Job modal default. */
  initialMode?: Mode
}
```

Internal state:
- `mode: Mode` — starts at `initialMode` or the prop-derived default
- The displayed input is the parent-supplied `value`; auto mode locks it and re-derives via `onChange` whenever contract or target changes

External invariants:
- `value` (the prop) is always the canonical ex-GST budget; the parent stores it as a single string
- The component never holds money in any other unit
- No backend interaction inside the component

Render anatomy:

```
Total budget                         (label)

  ┌────────────────────────────────────────────────────────┐
  │  ◉ Auto-calculate from target profit  ◯ Manual budget  │  (radios — visible only when target set)
  └────────────────────────────────────────────────────────┘

  [────────────] [ex|inc]                                    (GstAmountInput, locked in auto mode)
  Auto-calculated: $200,000.00 × (1 − 15.00%) = $170,000.00  (formula hint, auto mode only)

  ⚠ Budget exceeds target cost limit by $5,000.00.           (warning, manual mode only,
    Effective margin would be 5.00%, not 15.00%.              when budget > target_cost_limit)

  Budget is $5,000.00 below target cost limit                (informational, manual mode only,
   (more conservative than target).                          when budget < target_cost_limit)
```

When target is null: radios hidden, no formula hint, no warning — plain `<GstAmountInput>`. (Same as Phase 3 Lite+ today.)

### New Job modal (`Jobs.tsx`)

The modal currently has plain `<GstAmountInput>` for both contract value and total budget plus a plain `<input>` for status etc. Phase 3 Lite++ adds a `targetProfit` state and replaces the budget input with `<TotalBudgetField>`:

```
New Job

  Job name *           [────────────]
  Job code             [────────────]
  Site address         [────────────]
  Contract value       [200000.00] [ex|inc]
  Target profit %      [15.00]              ← NEW field on the modal
  Total budget         (TotalBudgetField — radios + locked input)
  Status               [Active ▼]

                          [Cancel] [Save]
```

Initial mode: `'auto'` (the modal has no existing data to detect). Both contract and target start empty; radios appear once the user enters a target. When the user types a contract + target, the budget auto-fills via the locked input.

Submit: existing `useCreateJob` mutation; the body adds `target_profit_ratio_pct` (already in `JobCreate` schema since Phase 3 Lite+ Batch 1).

### Job Settings form (`JobDetail.tsx`)

Replace the existing total-budget `<GstAmountInput>` field-group with `<TotalBudgetField>`. Compute `initialMode` once on mount:

```ts
const initialBudgetMode: Mode = (() => {
  if (job.target_profit_ratio_pct === null || job.contract_value_ex_gst === null) {
    return 'manual'
  }
  const expected = autoCalc(job.contract_value_ex_gst, job.target_profit_ratio_pct)
  return budgetsMatchToCent(job.total_budget_ex_gst, expected) ? 'auto' : 'manual'
})()
```

Pass `initialMode` to the field. After mount, the component owns the mode.

The Job Settings form's other fields (target profit %, warning thresholds) and the existing Target margin panel are **unchanged**.

### Mode-selection rules (frozen)

| Surface | `target_profit_ratio_pct` | `contract_value_ex_gst` | Stored `total_budget_ex_gst` | Initial mode | Radios visible? |
|---|---|---|---|---|---|
| New Job modal | (no stored data) | (no stored data) | (no stored data) | **auto** (component default) | only after target is entered |
| Settings — target null | null | any | any | **manual** | no |
| Settings — target set, contract null | set | null | any | **manual** (auto needs both) | yes (Auto disabled with hint) |
| Settings — target set, contract set, budget matches calc | set | set | within ±$0.01 of calc | **auto** | yes ("Auto" selected) |
| Settings — target set, contract set, budget differs | set | set | differs from calc | **manual** override | yes ("Manual" selected) |

### Live re-derivation rules (frozen)

When in **auto mode** (radios available + "Auto" selected):
* User types in `contract_value_ex_gst` → budget recomputes on every keystroke; locked input shows new value; `onChange` emits new value upward
* User types in `target_profit_ratio_pct` → same
* User clears contract OR target → auto-calc no longer possible → component flips to **manual mode** (radios remain visible, "Manual" auto-selected, current displayed budget preserved as a draft); the "Auto" radio is disabled with a hint "Set contract value and target profit % to enable auto-calculate"
* User flips radio to "Manual" → input becomes editable, current displayed value preserved as the starting editable draft

When in **manual mode**:
* Input is editable; user types whatever
* Inline warning re-derives below the input on every keystroke, comparing against `contract × (1 − target/100)` if both are set
* User flips radio to "Auto" → input snaps to the auto-calc value (the prior manually-typed draft is discarded silently — the radio click is the user's explicit choice; Save hasn't been clicked yet so they can flip back)
* User clears contract or target → mode stays manual (the manual value remains the draft)

### Tolerance check (frozen)

`budgetsMatchToCent(stored, computed)` compares two Decimal-strings as cents:

```ts
function budgetsMatchToCent(a: string | null, b: string | null): boolean {
  if (a === null || b === null) return false
  const aCents = Math.round(Number(a) * 100)
  const bCents = Math.round(Number(b) * 100)
  return aCents === bCents
}
```

Conversion to cents-as-int avoids float equality drift (`90.91 - 90.91` could surface as `1.4e-14` from `Number()` arithmetic). `Math.round` quantizes to the nearest cent before integer comparison, matching how the backend `_compute_margin_fields` quantizes its derivations to `Decimal("0.01")`.

### Submit behaviour

The form sends whatever value is currently in the budget input field, via the existing PATCH/POST endpoints:
* Auto mode → sends the live-computed `contract × (1 − target/100)` quantized to 0.01
* Manual mode → sends the typed value
* Clearing the budget input via empty-string → sends `null` (Phase 3 Lite+ correction `d3ce2bc` handles the explicit-null clear)

### What does NOT change in the UI

* Top dl money formatting (Phase 3 Lite+ Batch 3) — unchanged
* KPI header including Target profit % tile — unchanged
* Budget vs actual category breakdown panel — unchanged
* Target margin panel — **unchanged**. In auto mode the delta line will read "matches target cost limit exactly" (i18n key `budget.your_budget_delta_match`, already added Batch 3).
* Chip routing, GST display preference, Add Category Budget form — none touched.

---

## Formulas

```
# All values ex-GST as Decimal-strings; quantize to 0.01.

# Auto-calc (used by the field in Auto mode AND by the override-warning
# render in Manual mode):
target_cost_limit_ex_gst = contract_value_ex_gst × (100 − target_profit_ratio_pct) / 100

# Override delta (frontend pre-Save preview; backend computes the same
# field post-Save as `budget_delta_vs_target_cost_ex_gst`):
delta_ex_gst = total_budget_ex_gst − target_cost_limit_ex_gst
# positive => budget exceeds target cost limit => lower margin than target
# negative => budget below target cost limit => more conservative

# "Effective target margin given chosen budget" (frontend pre-Save preview;
# backend computes the same field post-Save as `budgeted_profit_ratio_pct`):
effective_margin_pct = (contract_value_ex_gst − total_budget_ex_gst) / contract_value_ex_gst × 100
```

Frontend computation lives next to the `<TotalBudgetField>` component; it produces the same numbers as the backend's `_compute_margin_fields` because both use the same formula and the same `Decimal("0.01")` quantization (the frontend via `Math.round(... * 100) / 100` since JS lacks native Decimal).

---

## Test strategy

### Backend

**No backend changes → no new backend tests required.** The existing 525 tests continue to cover everything. Per operator instruction.

### Manual E2E (required, against live `daefdeef-…` 晶晶 with PATCH+SQL-reset round-trip)

Six cases, frozen by the operator's E2E checklist:

1. **New job: contract + target auto-fills budget.**
   - Open New Job modal, enter Job name "Phase3LitePP NewAuto", contract = $200,000, target = 15%.
   - Verify the locked budget input shows $170,000.00 with the "Auto-calculated: …" formula hint.
   - Save. Confirm the persisted job has `total_budget_ex_gst = 170000.00`.
   - Cleanup: SQL DELETE on the new job (jobs are not API-deletable; SQL is the only path).

2. **New job: manual override works.**
   - Open New Job modal, enter contract + target as in (1), then click "Manual budget" radio.
   - Input becomes editable; current value (auto-calc $170,000) is preserved as the starting draft.
   - Type $195,000. Inline warning shows "Budget exceeds target cost limit by $25,000. Effective margin would be 2.50%, not 15%."
   - Save. Confirm persisted job has `total_budget_ex_gst = 195000.00`.
   - Cleanup: SQL DELETE.

3. **Existing job: auto mode detection works.**
   - On 晶晶, set target = 15% and contract = $200,000 via Settings, save (this puts target+contract on file but budget remains $188,000 — which differs from the auto-calc $170,000, so the form should open in **override** mode next time).
   - Re-open Settings → confirm "Manual" radio is selected and the budget input shows $188,000 with the override warning.
   - Then: temporarily set budget = $170,000 (matches auto-calc), save.
   - Re-open Settings → confirm "Auto" radio is selected and the input is locked at $170,000 with the formula hint.
   - Cleanup: SQL reset (target=null, contract=null, budget back to $188,000).

4. **Existing job: override mode shows delta warning.**
   - Same setup as (3) with budget = $188,000 ≠ auto-calc $170,000.
   - Confirm the warning text shows positive delta and reduced margin %.
   - Cleanup as (3).

5. **Switching manual → auto snaps budget to formula.**
   - Settings form with target=15, contract=$200,000, budget=$195,000 (manual mode).
   - Click "Auto" radio → input snaps to $170,000 (locked).
   - Click "Manual" radio → input becomes editable with $170,000 as the draft (the prior $195,000 is discarded by design — the radio click is the user's choice).
   - Cleanup as (3).

6. **Clearing target/contract returns to manual without deleting budget.**
   - Settings form with target=15, contract=$200,000, budget=$170,000 (auto mode).
   - Clear the contract value → "Auto" radio becomes disabled with the hint, mode flips to "Manual"; budget input retains $170,000 as a draft (does NOT clear to empty).
   - Re-enter contract = $250,000 → if "Auto" is reselected, input snaps to new auto-calc $212,500.
   - Cleanup as (3).

### Regression gate

* `pytest -q` — must remain at 525 green
* `npx tsc --noEmit` — clean
* `npm run build` — clean

### Live verification protocol

Same as Phase 3 Lite+ Batch 3: PATCH+SQL-reset round-trip on 晶晶 so the operative baseline is preserved at the end of the walk. New jobs created during E2E case (1) and (2) are SQL-deleted at cleanup since the API has no DELETE on jobs.

---

## Migration impact

**None.** No schema change, no migration revision, no data backfill. Existing data preserved verbatim.

---

## Critical files

### New

* `admin/src/components/TotalBudgetField.tsx` — shared component encapsulating the auto/manual mode state machine, radios, locked/editable input wrapper around `<GstAmountInput>`, formula hint, override warning. ~150 lines including JSDoc.

### Modified

**Frontend:**
* `admin/src/pages/Jobs.tsx` — New Job modal swaps the budget `<GstAmountInput>` for `<TotalBudgetField>`; adds a `targetProfit` state + form field; submit body includes `target_profit_ratio_pct`.
* `admin/src/pages/JobDetail.tsx` — `JobSettingsForm` swaps the budget `<GstAmountInput>` for `<TotalBudgetField>`, computes `initialMode` once on mount.
* `admin/src/i18n/en.json` + `admin/src/i18n/zh.json` — ~7 new keys:
  - `budget.mode_auto` — "Auto-calculate from target profit" / "根据目标利润自动计算"
  - `budget.mode_manual` — "Manual budget" / "手动设置预算"
  - `budget.auto_disabled_hint` — "Set contract value and target profit % to enable auto-calculate." / "请先设置合同金额和目标利润率,才能启用自动计算。"
  - `budget.auto_formula_hint` — "Auto-calculated: {{contract}} × (1 − {{target}}) = {{result}}" / "自动计算:{{contract}} × (1 − {{target}}) = {{result}}"
  - `budget.override_warning_over` — "Budget exceeds target cost limit by {{delta}}. Effective margin would be {{ratio}}, not {{target}}." / "当前预算超出目标成本上限 {{delta}}。实际利润率 {{ratio}},而非目标 {{target}}。"
  - `budget.override_info_under` — "Budget is {{delta}} below target cost limit (more conservative than target)." / "当前预算比目标成本上限低 {{delta}}(比目标更保守)。"
  - `budget.target_profit_input_label` — "Target profit %" / "目标利润率 %" — already exists as `jobs.target_profit_ratio_pct` from Phase 3 Lite+ Batch 2; reused, no new key.

### Not modified

* Backend (models, schemas, services, API, tests, migrations) — **zero touches**
* `admin/src/lib/budget.tsx`, `admin/src/lib/gst.ts` — reused as-is
* `admin/src/store/gstDisplay.ts` — unchanged
* `admin/src/components/AppShell.tsx`, `<GstAmountInput>` — reused as-is (the `disabled` prop on `<GstAmountInput>` already exists from Batch 2 and provides the locked state)
* `mobile/` — no Expo changes

---

## Out of scope (deferred)

* **Backend-derived budget column / `budget_source` enum / `is_overridden` flag** — explicitly rejected. Storage stays single-source.
* **Auto-backfill of existing jobs** — explicitly rejected.
* **Recomputing on contract/target updates after Save** — Save commits whatever the form shows; later edits open the form in whichever mode the new stored values + auto-calc agree on.
* **Confirmation prompt on Manual→Auto snap** — explicitly out per "switching manual → auto snaps budget to formula" rule. The user's radio click is the explicit consent.
* **Apply auto-budget logic on the `/jobs` list page directly** — out of scope; per-job edits live in the modal (creation) and the settings form (edit).
* **Phase 4 work** — Excel export, accountant handoff. Separate phase.

---

## Batches

**One batch, one commit.** Frontend-only, no schema, no backend.

| Step | Scope |
|---|---|
| T-A | Create `<TotalBudgetField>` component with the auto/manual state machine, radio buttons, locked-input wrapper around `<GstAmountInput>`, formula hint, override warning |
| T-B | Wire `<TotalBudgetField>` into the New Job modal in `Jobs.tsx`; add `targetProfit` form state + input; extend submit body |
| T-C | Wire `<TotalBudgetField>` into `JobSettingsForm` in `JobDetail.tsx`; compute `initialMode` from loaded job data |
| T-D | Seven new i18n keys in `en.json` + `zh.json` |
| T-E | Manual E2E — six cases per the test strategy, against live 晶晶 (PATCH+SQL-reset round-trip) and against two ephemeral new jobs (SQL DELETE cleanup) |
| T-F | Regression gate (`pytest`, `tsc`, `npm run build`) and one commit |

**Exit:** Builder enters contract + target on either the New Job modal or Job Settings form, accepts the auto-calculated budget by default with no mental math; flips override on for the rare manual-budget case and sees the margin warning live; existing 晶晶 budget is unchanged on disk; backend pytest, admin tsc + build all green.

---

## Why this stays small

The operator feedback was one line + one scope correction: "don't make me do the arithmetic" + "and not just on the edit form, also on creation." The fix is one new shared component consumed in two places. No data shape changes, no API changes, no migration, no service changes, no backend test changes. The Target margin panel I built in Phase 3 Lite+ Batch 3 already shows everything needed to communicate the override consequences post-Save — so the new pre-Save work is bounded to picking the right starting mode, computing the locked-input value live from sibling fields, and switching between locked/editable.
