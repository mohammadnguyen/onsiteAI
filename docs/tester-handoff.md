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

## 10 example inputs to try (contributor)

Mix them into your own real entries — don't submit only these. Each is picked to exercise a different parser path the test plan expects. The **expect** column is what the parser *should* do; if it doesn't, that's an issue-log row.

| # | Input | Expect | Why this input |
|---|---|---|---|
| 1 | `$305 Bunnings Kelly bluemetal` | Saved (auto-reviewed). Amount 305, Job Kelly House, Supplier Bunnings, Category Earthworks. | Plan spec anchor #1 — EN reviewed happy path. |
| 2 | `$420 Bunnings Kelly concrete` | Saved. Amount 420, Job Kelly House, Supplier Bunnings, Category Concrete. | Different category via single EN keyword. |
| 3 | `工地1 水工材料 163` | Saved — pending review. Chips: `Amount uncertain`, `Supplier uncertain`. Job matched via zh alias, Category Plumbing. | Plan spec anchor #2 — zh path + bare-number amount. |
| 4 | `¥50 Kelly` | Saved — pending review. Chip: `Unsupported currency` (plus peers). Amount 50 extracted, Job Kelly. | Plan spec anchor #3 — non-AUD currency handling. |
| 5 | `bunnings Kelly 88 timber cash` | Saved (auto-reviewed). Supplier Bunnings (via `bunnings` alias), Amount 88, Job Kelly, Category Carpentry, Payment `cash`. | Exercises the lowercase alias path + EN payment method detection. |
| 6 | `Bunnings 250 Kelly 水泥 转账` | Saved (auto-reviewed). Amount 250, Job Kelly, Supplier Bunnings, Category Concrete (zh keyword 水泥), Payment `transfer` (zh 转账). | Mixed EN/zh — parser should handle both scripts in one line. |
| 7 | `Kelly $1,234.56 electrical sparky` | Saved (auto-reviewed). Amount 1234.56 (1000-separator survives), Category Electrical (two-keyword boost: `electrical` + `sparky`). | Tests decimal + thousand-separator + high-confidence category. |
| 8 | `Kelly 80` | Saved — pending review. Chips: `Amount uncertain`, `Supplier uncertain`, `Category uncertain`. Amount 80, Job Kelly. | Bare-minimum input — see how much the parser confidently extracts when most tokens are missing. |
| 9 | `$305 Bunnings Kelly bluemetal` (submit AGAIN) | Saved — pending review. Chip: `Duplicate suspected` (references expense #1). | Intentional duplicate — lets the admin practice the resolve-duplicate workflow and verify the duplicate-of link works. |
| 10 | `Harvey Norman Kelly 350 tiles` | Saved — pending review. Chips: `Supplier uncertain` (Harvey Norman has no alias yet) and possibly `Category uncertain`. | `alias-gap` probe — the admin gets to practice adding a new supplier + alias during queue resolve. |

After running through these, spend the rest of the trial typing **real entries** the way the builder actually types them on site. The 10 above are a warmup; the real value is in unscripted input.

---

## What the admin does during the window

Keep it loose. Roughly:

- Morning: resolve the overnight queue. Each item takes ~20 seconds if the parser did OK, ~60 seconds if there's a fix to make (supplier swap, category tweak, duplicate review). That's the main time sink.
- During resolve, if you're adding the same supplier alias twice in one week, note it as `alias-gap` — it's a signal we should build the "add-alias-from-review" action sooner.
- If you ever think "this was hard to find in the review panel" or "I wish I could edit X," log it as `review-friction`.
- End of day: quick pass through `/expenses` to sanity-check the reviewed set. Anything that looks wrong opens the audit log tab to see the diff.

Also run the [admin-flow checklist from internal-testing.md](internal-testing.md#admin-flow) once near the start of the window — it's a 10-minute sweep that catches wiring issues before they poison the usage data.

---

## Where to record issues

Single source: [`docs/internal-testing.md` → Issue log template](internal-testing.md#issue-log-template). Copy the template row into a shared spreadsheet or a simple markdown file both testers can edit. Keep one row per finding. Use the five tags exactly:

- `parser-miss`
- `alias-gap`
- `duplicate-false-positive`
- `review-friction`
- `unsupported-currency`

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

1. Count the log rows by tag. Result is five numbers:

   ```text
   parser-miss:               <n>
   alias-gap:                 <n>
   duplicate-false-positive:  <n>
   review-friction:           <n>
   unsupported-currency:      <n>
   ```

2. Re-read the **Top 3 highest-value fixes** section in [`docs/internal-testing.md`](internal-testing.md#top-3-highest-value-fixes-to-consider-after-12-weeks) with those counts in mind. Each candidate has a "signal to commit" threshold; check whether real usage cleared it.

3. Produce a ranked top-3 for the next build window based on **trial counts × severity**, not engineering preference. If the counts don't support any of the prediction top-3, the real top-3 is whatever the log actually contains.

4. Share the numbers + ranked top-3 before scheduling any follow-up code work. The trial is a data-collection exercise; it's not a commit unless the data justifies one.
