# Phase 2 internal-testing prep

Post–Batch 4c snapshot for internal-testing rollout.

1. [Clean-slate reset](#clean-slate-reset) — what to run before testers log in
2. [Internal testing checklist](#internal-testing-checklist) — admin + contributor flows to exercise
3. [Issue log template](#issue-log-template) — copy/paste rows when you spot something
4. [Post-trial decision framework](#post-trial-decision-framework) — **forked decision** between parser/review work (Branch A) and dashboard-first Phase 3 Lite (Branch B). Not a default; the branch is chosen from trial counts + business signals.

Keep this document terse. Add observations to the issue log, not to the prose.

---

## Clean-slate reset

> **Status for current trial window:** the dev DB was **not** reset cold before the trial opened — see [`docs/trial-baseline.md`](trial-baseline.md) for the locked t=0 snapshot (7 expenses present; 4 real 晶晶 entries counted, 3 Kelly House Claude E2E rows excluded) and the official start timestamp (`2026-04-24 09:31:17 UTC`). That baseline doc is the source of truth for the current window. **Do not run the reset script during this window** — it would delete real trial data.

The reset script below exists for **future trial windows** (after this one wraps, or before a new round that deliberately starts from empty). The E2E runs during Batches 4a and 4b seeded identical-amount, same-job, same-date expenses that would poison the duplicate detector if left in place alongside new real entries; running the reset between rounds keeps the duplicate signal clean.

```bash
cd backend
uv run python -m scripts.reset_testing_expenses
# Cleared N expense(s); cascaded M review queue row(s) and K audit log row(s).
```

**What's deleted:** `expenses`, `expense_review_queue`, `expense_audit_log`.

**What's preserved:** users, jobs, job aliases, category seeds, suppliers, supplier aliases.

Re-runs are safe no-ops.

---

## Internal testing checklist

Run both roles against the same dev backend. Treat every bullet as one manual step; check it off in the issue log below if you find a problem, otherwise just move on. Target: ~30 minutes per tester per round.

### Pre-flight (admin does this once before testers join)

- [x] Postgres on `:5433` (host) — container `sitetracker-db` (healthy at baseline).
- [x] DB state captured in [`docs/trial-baseline.md`](trial-baseline.md) at `2026-04-24 09:31:17 UTC`. **Do not reset during this window.** The 3 pre-baseline Claude E2E rows are already tagged for exclusion in that doc.
- [x] Admin seeded: `admin@example.com` / `admin` (from Phase 1 `seed_admin`).
- [x] Contributor seeded: `jeffrey@example.com` / `jeffpass`.
- [x] Kelly House job (seed) + 晶晶 job (user-added) present with their aliases. See `trial-baseline.md` for the full inventory.
- [x] Bunnings supplier with alias `bunnings`.
- [x] 23 category seeds present (from Phase 1).
- [ ] Backend on `:8000` — start with `cd backend && uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload` just before the trial opens.
- [ ] Admin on `:5173` — start with `cd admin && npm run dev` just before the trial opens.
- [ ] Add more real suppliers you expect the team to type (with short-form aliases), as the trial surfaces them — one of the things we want to measure is how often alias-gap fires, so don't pre-seed aggressively.

### Admin flow

- [ ] Log in → lands on `/expenses` with an empty list.
- [ ] `/suppliers` → create a new supplier with an alias → alias appears on that supplier's row.
- [ ] `/jobs` → pick a job → add an alias → verify it saves.
- [ ] `/review-queue` → open any queued item (after contributor rounds land some) → adjust supplier/category → **Approve** with a note → expense moves to reviewed, queue closes, audit row recorded.
- [ ] Reject a different queued item → expense soft-deletes, queue closes, audit row recorded.
- [ ] `/expenses` → click a reviewed expense → **Edit** → change any field with a reason → **Audit log** tab shows the diff + reason.
- [ ] Filter chips on `/expenses` (status, job, entered_by, date range, receipt_status) — each one narrows the list as expected.
- [ ] Language toggle → switch to 中文 → every surface flips. Round-trip back to EN is clean.
- [ ] Log out → `/login`.

### Contributor flow

- [ ] Log in → lands on `/capture`. (Not `/expenses`, not `/my-expenses`.)
- [ ] Paste a realistic raw expense (e.g. your own typical one-liner) → **Submit**.
  - Reviewed-path: result view shows **Saved**, correct amount/job/supplier/category. Record the raw input in the issue log if anything is wrong.
  - Pending-path: result view shows **Saved — pending review** + reason chips. Confirm each chip is actually a parser uncertainty you'd want an admin to check.
- [ ] Toggle "Add receipt later" → submit a second entry → `/my-expenses` should show the receipt-later flag surfaced somewhere (currently not on the row but on detail).
- [ ] Open the **Advanced (manual fields)** accordion → submit a third entry with a structured job/supplier override → confirm the override wins and the raw-text parser fills in the rest.
- [ ] `/my-expenses` → items split into **Pending review** and **Reviewed** sections; counts match what you submitted.
- [ ] Try to navigate to `/review-queue` → should show the "Access denied" shell with a link back to `/capture`.
- [ ] Try to navigate to a reviewed expense's `/expenses/:id` → confirm you can read your own but not edit (Edit button hidden when reviewed-and-not-admin).
- [ ] Language toggle → switch to 中文 → capture page, My Expenses, result view, chips all translate. Round-trip clean.

### Cross-role verification (do at least once per tester pair)

- [ ] Contributor submits something that goes to pending.
- [ ] Admin resolves it with a meaningful edit (e.g. set supplier, tweak category).
- [ ] Contributor refreshes `/my-expenses` → pending row has moved to reviewed.
- [ ] Admin opens the same expense's audit log → diff shows the changes with admin as the editor.

---

## Issue log template

Copy a row per issue into a shared spreadsheet (or keep it inline here — Phase 2 isn't at a scale where a tracker is necessary yet). Keep entries terse; one finding per row. Use one of the six issue types below as the **Type** column so triage can batch similar items.

**Issue types:**

| Type | Use when |
|---|---|
| `parser-miss` | Parser returns wrong amount, job, supplier, category, payment method, or description for a raw input. |
| `alias-gap` | A word/phrase the team types routinely fails to match an existing supplier or job because no alias is seeded. Separate from parser-miss because the fix is data, not code. |
| `duplicate-false-positive` | `duplicate_suspected` fires on two entries that are genuinely different transactions. |
| `review-friction` | Review queue workflow itself is clunky — missing field, confusing copy, slow load, translation gap, etc. |
| `unsupported-currency` | Non-AUD input is mishandled — amount extracted incorrectly, wrong chip raised, admin can't correct the value during resolve. |
| `visibility-gap` | Admin had to do math the system should have shown directly — adding expenses by hand to get job-to-date, computing remaining budget, flagging a category overspend mentally, or opening a calculator / spreadsheet / paper to answer "how much have we spent on X?". One row per occurrence. Also log the row when admin has to leave this tool to reach the answer (ledger book, accountant export, another app). **Optional but useful:** prefix the `notes` field with `scenario: <type>` so post-trial analysis can split the count by the kind of question that went unanswered. Use one of four types: `total_job_spend`, `remaining_budget`, `category_overspend`, `spend_question_x` (any "how much did we spend on X?" that isn't one of the first three). |

**Template row (copy one per finding):**

```markdown
| YYYY-MM-DD | type | role | raw-input-or-URL | expected | observed | severity | notes |
|------------|------|------|------------------|----------|----------|----------|-------|
```

**Worked example:**

```markdown
| 2026-04-25 | parser-miss     | contributor | "¥50 Kelly"                  | description empty after Kelly consumed by job matcher | description = "Kelly"    | low    | See Batch 4b report; cosmetic                           |
| 2026-04-26 | alias-gap       | contributor | "bazza 200 concrete"         | supplier = Harvey Norman                              | supplier_uncertain fires | medium | need alias "bazza" → Harvey Norman                      |
| 2026-04-27 | duplicate-fp    | admin       | two $120 Bunnings same day   | both approved (different jobs of the same project)    | duplicate_suspected fires| medium | rule fires on (job, amount, ±1 day, supplier)           |
| 2026-04-28 | review-friction | admin       | /review-queue                | can edit job from review panel                        | job is read-only         | low    | acknowledged — `ExpenseUpdate` omits `job_id` by design |
| 2026-04-29 | unsupported-ccy | contributor | "€50 Smith"                  | unsupported_currency chip + amount prefilled as 50    | chip fires, amount ok    | none   | working as intended — here for completeness            |
| 2026-04-30 | visibility-gap  | admin       | "Kelly House spend to date?" | dashboard tile shows running total                    | had to sum /expenses rows by hand; ~4 min | medium | scenario: total_job_spend — would have skipped the math if the job detail page showed it |
```

Fields:

- **Date** — first occurrence.
- **Type** — one of the six tags above.
- **Role** — admin / contributor.
- **Raw-input-or-URL** — the literal string the tester typed OR the UI location where friction was felt.
- **Expected** — what the tester thought would happen.
- **Observed** — what actually happened.
- **Severity** — none / low / medium / high. Reserve "high" for things that block a day's testing.
- **Notes** — workaround, linked issue, or context.

The goal is not to fix everything during the trial — it's to have a well-organized log so the post-trial triage can batch fixes into one or two small follow-up PRs.

---

## Post-trial decision framework

**Locked hard rule — do not dilute, do not reframe as a soft preference. This is the post-trial decision order unless the user explicitly changes it.**

After the window closes, the **first priority check is `visibility-gap`**. Parser / review work is NOT the default next build. The decision is a fork based on the `visibility-gap` count first, Branch A thresholds only if `visibility-gap` doesn't clear.

> **Baseline reference.** When counting log rows + expense rows after the trial closes, apply the row-classification rules in [`docs/trial-baseline.md`](trial-baseline.md). That doc captures the 7 expenses present in the dev DB at the official trial-start timestamp (`2026-04-24 09:31:17 UTC`) and notes which 3 are pre-trial Claude E2E noise that must be excluded from business interpretation.

### Step 1 — Count all six tags

Produce exactly these six numbers:

```text
parser-miss:               <n>
alias-gap:                 <n>
duplicate-false-positive:  <n>
review-friction:           <n>
unsupported-currency:      <n>
visibility-gap:            <n>
```

### Step 2 — Priority check: `visibility-gap`

Before evaluating any parser / review follow-up, explicitly evaluate whether `visibility-gap` justifies **Branch B — Phase 3 Lite dashboard / budget visibility**.

**Hard rule:**

- **If `visibility-gap >= 3`** → Branch B is the recommended next phase. The next Claude output **MUST begin with the exact phrase:**

  > **"Branch B is the recommended next phase."**

  Then prepare a focused Branch B plan using the seven-section scope locked in [Branch B plan shape](#branch-b-plan-shape) below. **Do not propose Branch A candidates. Do not default to Phase 2.5. Do not default to parser/review polish. Do not assume Branch A just because parser-related tags exist.**

  The **only** override is if capture is clearly breaking in a way that **materially blocks daily use** — e.g., admin cannot resolve a queue item at all, duplicate detector is firing so aggressively that approvals grind to a halt, or an RBAC / data-safety issue is surfacing. High bar. A `parser-miss` count of 8 is not enough. A `alias-gap` count of 15 is not enough. The override is reserved for "we literally cannot keep running the trial under these conditions" situations, not for "capture has friction."

- **If `visibility-gap < 3`** → proceed to Step 3 and evaluate Branch A normally against its existing thresholds.

### Step 3 — Branch A evaluation (only reached if `visibility-gap < 3`)

Justified when the trial data shows real pain in capture or triage. Each candidate has a threshold; meeting at least ONE threshold below justifies a Branch A build window. If no Branch A threshold clears either, pause and share the numbers — continuing the trial for another window is a reasonable outcome. Do not spin up a build just to have a build.

| Candidate | Threshold to commit | Effort |
|---|---|---|
| **Phase 2.5** — real Claude fallback behind the `LLMParser` interface | ≥ 5 distinct `parser-miss` findings plausibly LLM-rescueable (ambiguous descriptions, rare suppliers, mixed EN/zh phrasings without seeded keywords) | Small. Interface + mock already shipped — adds `anthropic` dep + real adapter + fixture tests + 20-input cost/latency measurement. |
| **Add-alias-from-review** — inline "save this as an alias" checkbox in the review resolve panel, atomic with the approve transaction | ≥ 10 `alias-gap` findings across all testers | Medium. New endpoint field; service writes the alias; UI adds one checkbox + toast. No schema change. |
| **Description fallback polish** — trim already-consumed tokens from `description`; label the supplier/description fallback column; add a `supplier_or_description` accessor | ≥ 3 `parser-miss` findings where description still contains matched tokens OR ≥ 3 `review-friction` findings confused by the fallback column | Small. One parser stage + two UI labels + one schema field. |

If two or three thresholds clear simultaneously, build them in the order above (Phase 2.5 first because it rescues multiple classes of issue with one change).

### Underlying reasoning for Branch B priority

**Dashboard visibility compounds.** Every expense entered from that point forward benefits from the new surface, including all historical entries. A parser fix only benefits the specific inputs it was designed to rescue. The arithmetic is: visibility gap × days in use = growing cost; parser miss × future identical inputs = bounded cost.

Visibility gaps cost minutes of ad-hoc math per question and block real business decisions (can we afford this subcontractor? are we over budget?). Parser issues cost ~30 seconds of admin picker-clicking per occurrence and are friction around a working flow. The priority order reflects the cost arithmetic, not engineering preference.

---

## Branch B plan shape

When `visibility-gap >= 3` triggers Branch B, the plan Claude produces **must cover only** the seven sections below and must **not** include any of the explicitly-out-of-scope items. Any broader or adjacent work is a separate phase.

### In scope (seven sections)

1. **Jobs list cost / budget visibility** — each row on `/jobs` shows total cost to date, total budget, remaining budget, % consumed, overspend flag (red when remaining < 0 or % > 100). Sorted by % consumed descending so the highest-risk job surfaces first.
2. **Job detail KPI header** — header row on `/jobs/:id` showing total spent (inc GST + ex GST), total budget, remaining, % consumed.
3. **Category actual vs budget table** — on the same job detail page, one row per category with actual ex-GST vs budget ex-GST, remaining, overspend flag.
4. **Minimal backend aggregation endpoints** — one endpoint for the job-detail KPIs + category split (e.g. `GET /jobs/{id}/budget-summary`), extension of `GET /jobs` to include per-row totals. No new schema; reads from existing `expenses` + `job_category_budgets` + `jobs.total_budget`.
5. **Minimal admin web surfaces** — restyle `admin/src/pages/Jobs.tsx` + `admin/src/pages/JobDetail.tsx` to surface the new numbers. No new routes.
6. **Test strategy** — backend unit + integration tests for the aggregation math (ex/inc GST splits, rejected-row exclusion, empty-job edge cases, pending-vs-reviewed treatment). Frontend verified via Claude Preview E2E per prior batches.
7. **Execution batches** — batch breakdown: (1) backend aggregation + tests, (2) admin UI wiring + i18n, (3) manual E2E + regression gate + commit.

### Out of scope for Branch B

These are **explicitly excluded** from any Branch B plan. If they're needed, they ship in a separate phase.

- Claude fallback (Phase 2.5)
- Review queue expansion (add-alias-from-review, resolve-panel extensions, description polish)
- Excel export (Phase 4)
- iOS / TestFlight work (Phase 6)
- Attachments (Phase 5)
- Notifications
- Labour attendance (Phase 5)

### Also deferred to full Phase 3 (not Lite)

These ship **after** Phase 3 Lite answers the budget question for real. Not part of the first Branch B build.

- Top-5 suppliers / top-5 categories roll-ups
- Monthly trend chart
- Estimated margin (`contract_value − total cost`)
- Reviewed-vs-pending banding on the totals
- Drill-down from a KPI to the feeding expense list

---

## What this pass explicitly does not do

- **No code** — this pass is trial preparation only. Any build (Branch A or Branch B) starts after the window closes and the decision framework above has picked the branch.
- **No Phase 2.5 yet** — listed above as a Branch A candidate; commits only if its threshold clears.
- **No Phase 3 Lite yet** — listed above as the Branch B candidate; commits only if the branch signals justify it.
- **No full Phase 3** (top-5 rollups, monthly trend, margin, banding) — out of Lite scope. Ships only after Lite answers the budget question for real.
- **No Phase 5** (receipt attachments, labour attendance) — Phase 5 scope.
- **No mobile/Expo feature work** — preserved per Batch 4c README; resumes after web-first validation succeeds.
- **No DB migrations** — the reset is a `DELETE`, not a schema change.
- **No backend tests** — the reset script is pure ops tooling; the test suite has its own fixtures (`tests/conftest.py`) that never touch the dev DB.

---

## Escalation

If a `high` severity issue comes up during testing and needs immediate attention, ping before waiting for the batch triage. Signs that merit interrupting:

- Data loss (expense disappears, audit log missing)
- Contributor can edit/delete expenses they don't own (RBAC breach)
- Admin can't resolve a queue item (workflow blocker)
- Login fails intermittently for correct credentials (regression in auth)

Everything else goes into the log and gets triaged together at the end of the trial.
