# Tester handoff — Phase 2 first internal trial

Short pack for the first 3–5 day internal trial. One admin + one contributor. Everything else lives in [`docs/internal-testing.md`](internal-testing.md) — if something here is ambiguous, that's the source of truth.

---

## Who / where / when

- **Admin:** 1 person (operator of the admin surfaces + triage)
- **Contributor:** 1 person (real typing on the capture surface)
- **Window:** 3–5 days. Keep it short; the point is to surface patterns, not stress-test.
- **No new feature work during the window.** No Phase 2.5, no Phase 3. Bug log grows, code stays still.

---

## Access

### Admin

- **URL:** <http://127.0.0.1:5173>
- **Email:** `admin@example.com`
- **Password:** `admin`
- **Lands on:** `/expenses` after login
- **Full nav:** New Expense · Expenses · Review Queue · Jobs · Users · Suppliers

### Contributor

- **URL:** <http://127.0.0.1:5173> (same bundle, role-aware routing does the rest)
- **Email:** `jeffrey@example.com`
- **Password:** `jeffpass`
- **Lands on:** `/capture` after login
- **Limited nav:** New Expense · My Expenses (anything else → Access denied shell)

### Backend + DB (for the operator — testers don't need to see this)

- Backend: `cd backend && uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload`
- Admin dev server: `cd admin && npm run dev`
- Postgres: Docker container `sitetracker-db` on host port `5433`
- Docs: <http://127.0.0.1:8000/docs> (interactive OpenAPI)

Start backend + admin just before the window opens. Leave Postgres up.

---

## Baseline data present (as of 2026-04-24)

The reset already ran; the DB is clean of expense test data.

- **Jobs:** `Kelly House` (code `KH-01`) with aliases `Kelly` (EN) and `工地１` (ZH — full-width digit, but NFKC normalization means `工地1` ASCII digit works too).
- **Suppliers:** `Bunnings` with alias `bunnings`.
- **Categories:** all 23 Phase 1 seeds (Plumbing, Carpentry, Concrete, Earthworks, Electrical, etc.).
- **Users:** admin + contributor as above.
- **Expenses / queue / audit:** empty. All entries created during the trial are real trial data.

If you want more jobs or suppliers before the window opens, create them through the admin UI — do not pre-seed aliases aggressively. One of the things we're measuring is **how often `alias-gap` fires**; if every alias is pre-seeded, we learn nothing.

---

## Payment method + GST rule

Every entry has a payment selector above the receipt-later checkbox: **Auto** / **Cash** / **Bank transfer**.

* **Auto** (default) — if the raw text contains a payment keyword (`cash` / `eft` / `transfer` / `bank` / `paid` / 现金 / 转账 / 银行) the parser extracts it. Otherwise the row is stored as `unknown` and the admin sets it during resolve.
* **Cash** or **Bank transfer** — explicit user choice, wins over parser extraction.

**GST rule:** cash payments are treated as **GST-exclusive** — the typed amount becomes both `amount_inc_gst` and `amount_ex_gst`, and `gst_amount` is `$0.00`. Small cash builder purchases usually lack a tax invoice so the GST input credit can't be claimed; carrying a phantom GST figure would misrepresent the books. All other payment methods (transfer, unknown) keep the standard 1/11 split.

The result view now shows three amount rows: **Amount (inc GST)**, **Amount (ex GST)**, **GST** — so the rule's outcome is visible on every save.

## 12 example inputs to try (contributor)

Mix them into your own real entries — don't submit only these. Each is picked to exercise a different parser path. The **expect** column is what the parser *should* do; if it doesn't, that's an issue-log row.

| # | Input | Expect | Why this input |
|---|---|---|---|
| 1 | `$305 Bunnings Kelly bluemetal` | Saved (auto-reviewed). Amount inc 305, ex 277.27, GST 27.73 (standard split, Payment `unknown`), Job Kelly House, Supplier Bunnings, Category Earthworks. | Plan spec anchor #1 — EN reviewed happy path. |
| 2 | `$420 Bunnings Kelly concrete` | Saved. Amount 420 / 381.82 / 38.18, Payment `unknown`, Category Concrete. | Different category via single EN keyword. |
| 3 | `工地1 水工材料 163` | Saved — pending review. Chips: `Amount uncertain`, `Supplier uncertain`. Job matched via zh alias, Category Plumbing. | Plan spec anchor #2 — zh path + bare-number amount. |
| 4 | `¥50 Kelly` | Saved — pending review. Chip: `Unsupported currency` (plus peers). Amount 50 extracted, Job Kelly. | Plan spec anchor #3 — non-AUD currency handling. |
| 5 | `bunnings Kelly 88 timber cash` | Saved (auto-reviewed). Amount inc 88, ex **88.00**, GST **0.00** (cash rule). Supplier Bunnings (via `bunnings` alias), Category Carpentry, Payment `cash`. | Lowercase alias + EN cash keyword → GST-exclusive split. |
| 6 | `Bunnings 250 Kelly 水泥 转账` | Saved (auto-reviewed). Amount inc 250, ex 227.27, GST 22.73 (standard split — transfer), Category Concrete (zh keyword 水泥), Payment `transfer` (zh 转账). | Mixed EN/zh, confirms the transfer path keeps the standard split. |
| 7 | `bunnings $500 现金 Kelly bluemetal` | Saved. Amount inc 500, ex **500.00**, GST **0.00** (cash rule). Supplier Bunnings, Category Earthworks, Payment `cash` (from 现金). | The user-provided cash example — zh keyword drives the GST rule. |
| 8 | `水泥 $1000 转账 Kelly` | Saved — pending review (supplier_uncertain — no supplier in text). Amount inc 1000, ex **909.09**, GST **90.91** (standard split — transfer). Category Concrete, Payment `transfer`. | The user-provided transfer example — zh 转账 drives standard 1/11 split. |
| 9 | `Kelly $1,234.56 electrical sparky` | Saved (auto-reviewed). Amount 1234.56 (1000-separator survives), ex 1122.33, GST 112.23, Category Electrical. | Tests decimal + thousand-separator + high-confidence category. |
| 10 | `Kelly 80` — then pick **Cash** radio before submit | Saved — pending review (amount/supplier/category uncertain). Payment `cash` (UI override), GST 0. | Confirms the UI picker overrides an absent parser extraction. |
| 11 | `$305 Bunnings Kelly bluemetal` (submit AGAIN) | Saved — pending review. Chip: `Duplicate suspected` (references expense #1). | Intentional duplicate — admin workflow practice. |
| 12 | `Harvey Norman Kelly 350 tiles` | Saved — pending review. Chips: `Supplier uncertain` (Harvey Norman has no alias yet) and possibly `Category uncertain`. | `alias-gap` probe — admin adds a new supplier + alias during resolve. |

After running through these, spend the rest of the trial typing **real entries** the way the builder actually types them on site. The list above is a warmup; the real value is in unscripted input.

> **Tip:** The user-provided examples above use `Kelly` as the job name; the user's original request used their own job alias `晶晶家`. For the trial, either (a) create a new job via the admin UI and give it the alias you actually type (`晶晶家`, site addresses, nicknames — whatever the team uses), or (b) use the seeded `Kelly` / `工地1` / `工地１` aliases. The parser doesn't care which job exists; it just needs *some* seeded alias to match.

---

## What the admin does during the window

Keep it loose. Roughly:

- Morning: resolve the overnight queue. Each item takes ~20 seconds if the parser did OK, ~60 seconds if there's a fix to make (supplier swap, category tweak, duplicate review). That's the main time sink.
- During resolve, if you're adding the same supplier alias twice in one week, note it as `alias-gap` — it's a signal we should build the "add-alias-from-review" action sooner.
- If you ever think "this was hard to find in the review panel" or "I wish I could edit X," log it as `review-friction`.
- **If you catch yourself computing a total by hand — adding expense rows to get job-to-date spend, subtracting to get remaining budget, scanning a category for overspend, or opening a calculator / spreadsheet / paper / another app to answer "how much have we spent on X?" — log it as `visibility-gap`, one row per occurrence.** This is the signal that the tool is missing the dashboard the business actually needs. Three of these across the trial makes Phase 3 Lite the next build.
- End of day: quick pass through `/expenses` to sanity-check the reviewed set. Anything that looks wrong opens the audit log tab to see the diff.

Also run the [admin-flow checklist from internal-testing.md](internal-testing.md#admin-flow) once near the start of the window — it's a 10-minute sweep that catches wiring issues before they poison the usage data.

---

## Where to record issues

Single source: [`docs/internal-testing.md` → Issue log template](internal-testing.md#issue-log-template). Copy the template row into a shared spreadsheet or a simple markdown file both testers can edit. Keep one row per finding. Use the six tags exactly:

- `parser-miss`
- `alias-gap`
- `duplicate-false-positive`
- `review-friction`
- `unsupported-currency`
- `visibility-gap` — the admin had to calculate a total (job spend, remaining budget, category overspend) by hand, or leave the tool (calculator, spreadsheet, accountant export, paper) to answer "how much have we spent on X?". One row per occurrence. **This is the trigger that justifies going to dashboards instead of more parser work — if the admin hits this 3+ times, Branch B gets priority.**

If you're unsure which tag fits, pick one and add a `notes` hint — triage can re-tag at the end.

**Don't try to fix issues during the window.** The discipline here is to accumulate a log. The post-trial summary (below) is what drives what gets built next.

---

## Escalate immediately (don't wait for batch triage) if

Copied from the main doc for convenience:

- Data loss (expense disappears, audit log missing).
- Contributor can edit or delete expenses they don't own (RBAC breach).
- Admin can't resolve a queue item (workflow blocker — can't approve AND can't reject).
- Login fails for valid credentials more than once in a row.

Anything else → log and keep going.

---

## After the window closes

1. Count the log rows by tag. Result is six numbers:

   ```text
   parser-miss:               <n>
   alias-gap:                 <n>
   duplicate-false-positive:  <n>
   review-friction:           <n>
   unsupported-currency:      <n>
   visibility-gap:            <n>
   ```

2. Walk the **[Post-trial decision framework](internal-testing.md#post-trial-decision-framework)** in `docs/internal-testing.md`. It forks into two explicit branches and the next build is **not** automatically more parser / review work:

   - **Branch A — parser / review work** (Phase 2.5 Claude fallback / add-alias-from-review / description polish). Each candidate has its own threshold on the capture counts above. Commits only if a threshold clears.
   - **Branch B — Phase 3 Lite dashboard / budget visibility.** Primary trigger: **`visibility-gap` ≥ 3**. The admin had to do spend math by hand or leave the tool to answer a budget question three or more times — that's the observable signal that the missing dashboard is blocking real work.

3. Share (a) the six numbers, (b) the admin's lived experience during the trial (was capture painful or smooth?), and (c) which branch seems justified per the framework's Step-3 resolution rules. The branch decision is made together, not by rule alone — the framework exists to prevent defaulting to parser polish out of engineering inertia.

4. No code work starts until the branch is picked. The trial is a data-collection exercise; the build that follows is scoped to the chosen branch.
