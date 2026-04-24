# Phase 2 internal-testing prep

Post–Batch 4c snapshot for internal-testing rollout. Three sections:

1. [Clean-slate reset](#clean-slate-reset) — what to run before testers log in
2. [Internal testing checklist](#internal-testing-checklist) — admin + contributor flows to exercise
3. [Issue log template](#issue-log-template) — copy/paste rows when you spot something
4. [Top 3 highest-value fixes to consider after 1–2 weeks](#top-3-highest-value-fixes-to-consider-after-12-weeks) — decision queue

Keep this document terse. Add observations to the issue log, not to the prose.

---

## Clean-slate reset

The E2E runs during Batches 4a and 4b seeded identical-amount, same-job, same-date expenses. That poisons the duplicate detector during real testing — the `duplicate_suspected` review reason fires against test junk, not against genuine repeats.

Before inviting testers, wipe expenses (review queue + audit log cascade automatically):

```bash
cd backend
uv run python -m scripts.reset_testing_expenses
# Cleared N expense(s); cascaded M review queue row(s) and K audit log row(s).
```

**What's deleted:** `expenses`, `expense_review_queue`, `expense_audit_log`.

**What's preserved:** users, jobs, job aliases, category seeds, suppliers, supplier aliases. The org setup testers need (Bunnings, Kelly House + its zh alias, categories, admin + contributor accounts) stays intact.

Re-runs are safe no-ops. If testers generate their own junk during the trial, run it again between rounds.

---

## Internal testing checklist

Run both roles against the same dev backend. Treat every bullet as one manual step; check it off in the issue log below if you find a problem, otherwise just move on. Target: ~30 minutes per tester per round.

### Pre-flight (admin does this once before testers join)

- [ ] Backend on `:8000`, admin on `:5173`, Postgres on `:5433` (host).
- [ ] `uv run python -m scripts.reset_testing_expenses` — dev DB expense-clean.
- [ ] Admin seeded: `admin@example.com` / `admin`.
- [ ] Contributor seeded via admin UI: one real builder per tester — their name + real email + a temp password.
- [ ] At least one job exists with EN + zh aliases covering the real site names the team actually uses.
- [ ] At least 5–10 real suppliers you expect to see, each with aliases for the short-form names the team types.
- [ ] 23 category seeds present (from Phase 1).

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

Copy a row per issue into a shared spreadsheet (or keep it inline here — Phase 2 isn't at a scale where a tracker is necessary yet). Keep entries terse; one finding per row. Use one of the five issue types below as the **Type** column so triage can batch similar items.

**Issue types:**

| Type | Use when |
|---|---|
| `parser-miss` | Parser returns wrong amount, job, supplier, category, payment method, or description for a raw input. |
| `alias-gap` | A word/phrase the team types routinely fails to match an existing supplier or job because no alias is seeded. Separate from parser-miss because the fix is data, not code. |
| `duplicate-false-positive` | `duplicate_suspected` fires on two entries that are genuinely different transactions. |
| `review-friction` | Review queue workflow itself is clunky — missing field, confusing copy, slow load, translation gap, etc. |
| `unsupported-currency` | Non-AUD input is mishandled — amount extracted incorrectly, wrong chip raised, admin can't correct the value during resolve. |

**Template row (copy one per finding):**

```markdown
| YYYY-MM-DD | type | role | raw-input-or-URL | expected | observed | severity | notes |
|------------|------|------|------------------|----------|----------|----------|-------|
```

**Worked example:**

```markdown
| 2026-04-25 | parser-miss     | contributor | "¥50 Kelly"                  | description empty after Kelly consumed by job matcher | description = "Kelly" | low    | See Batch 4b report; cosmetic                        |
| 2026-04-26 | alias-gap       | contributor | "bazza 200 concrete"         | supplier = Harvey Norman                              | supplier_uncertain fires | medium | need alias "bazza" → Harvey Norman                   |
| 2026-04-27 | duplicate-fp    | admin       | two $120 Bunnings same day   | both approved (different jobs of the same project)    | duplicate_suspected fires | medium | rule fires on (job, amount, ±1 day, supplier)        |
| 2026-04-28 | review-friction | admin       | /review-queue                | can edit job from review panel                        | job is read-only         | low    | acknowledged — `ExpenseUpdate` omits `job_id` by design |
| 2026-04-29 | unsupported-ccy | contributor | "€50 Smith"                  | unsupported_currency chip + amount prefilled as 50    | chip fires, amount ok    | none   | working as intended — here for completeness          |
```

Fields:

- **Date** — first occurrence.
- **Type** — one of the five tags above.
- **Role** — admin / contributor.
- **Raw-input-or-URL** — the literal string the tester typed OR the UI location where friction was felt.
- **Expected** — what the tester thought would happen.
- **Observed** — what actually happened.
- **Severity** — none / low / medium / high. Reserve "high" for things that block a day's testing.
- **Notes** — workaround, linked issue, or context.

The goal is not to fix everything during the trial — it's to have a well-organized log so the post-trial triage can batch fixes into one or two small follow-up PRs.

---

## Top 3 highest-value fixes to consider after 1–2 weeks

These are the candidates most likely to pay off based on the Batch 4b + 4c findings and the out-of-scope items the plan deliberately deferred. Rank is a prediction; real usage data will re-rank them.

### 1. Ship Phase 2.5 (real Claude fallback behind `LLMParser`)

**Why this ranks first.** The parser is rules-only today. Every row in the issue log tagged `parser-miss` or `alias-gap` is a candidate for the LLM fallback to rescue — it's the single change that reduces multiple classes of issue at once. The interface is already shipped (`backend/app/services/parser/llm_adapter.py`); Phase 2.5 just drops in `ClaudeLLMParser(LLMParser)` and activates it via `ANTHROPIC_API_KEY`.

**Effort:** small — the plan already scoped it (see [`docs/phase-2-plan.md` → Phase 2.5](#) if/when it's added; the plan lives in the user's plan history today). Add `anthropic` dependency, implement the adapter, record fixtures, measure cost/latency on 20 real entries, keep or defer.

**Signal to commit:** trial surfaces ≥ 5 distinct parser-miss findings that would plausibly be handled by an LLM (ambiguous descriptions, rare supplier names, mixed EN/zh phrasings without seeded keywords).

### 2. Admin "add alias from review queue" inline action

**Why this ranks second.** Every `alias-gap` issue has the same resolution path: the admin already has the expense, the supplier (or job), and the raw text in front of them in the review panel. Today they have to approve, then go to `/suppliers` or `/jobs`, find the row, open the alias form, type the alias from memory. That's four context switches per gap, and teams that find five gaps a week will stop bothering.

**What to build:** in the review detail panel, when the admin picks a supplier/job to assign, surface a "Save 'X' as alias for this supplier/job" checkbox next to the picker. Checked + approve → atomically write the alias row *in the same transaction* as the expense patch + queue close + audit.

**Effort:** medium. New endpoint field on the resolve payload; service writes the alias; UI adds one checkbox and one toast. Does not need a schema change on the alias tables.

**Signal to commit:** trial surfaces ≥ 10 `alias-gap` findings across all testers. Fewer than that and the four-clicks workaround is fine.

### 3. Description fallback + supplier-first review

**Why this ranks third.** The Batch 4b E2E surfaced a cosmetic parser issue (`Kelly` appearing in description after being consumed by the job matcher) and a display-logic quirk (`/my-expenses` supplier column falls back to description when supplier is null — works, but reads oddly). Neither blocks anyone; both will generate `parser-miss` or `review-friction` log rows.

**What to do:** (a) in `parser/description.py` (to be added), remove already-consumed tokens from the description result so it contains only unmatched tokens; (b) in `Capture.tsx`'s result view and `MyExpenses.tsx`'s row, label the fallback column "Supplier / description" so the behaviour is visible; (c) add a `supplier_or_description` derived accessor on the expense schema so clients don't re-implement the fallback.

**Effort:** small. One parser stage; two UI labels; one schema field.

**Signal to commit:** ≥ 3 `parser-miss` findings where description still contains tokens claimed by other matchers, or ≥ 3 `review-friction` findings confused by the supplier/description column.

---

## What this pass explicitly does not do

- **No Phase 3** (dashboards) — blocked behind "Phase 2 parser is good enough for real use."
- **No Phase 2.5** — listed above as a candidate, not scheduled.
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
