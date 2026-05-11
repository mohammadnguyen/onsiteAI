# Phase 4 — Excel export / accountant handoff

> **Direction:** the next phase from the V1 roadmap (`README.md`):
>
> > **Phase 4** — Excel export (All Expenses sheet + one sheet per job) — accountant handoff works.
>
> Driven by the practical handoff need: at BAS time (or at any reconciliation point) the builder gives their accountant a single file with every confirmed expense booked under the relevant jobs, in a shape the accountant can open in Excel and reconcile against bank statements + invoices. SiteTracker is the source of truth; the Excel file is the accountant's read surface.
>
> **Operator-decided scope (post-review revisions):**
>
> 1. **Default inclusion rule is reviewed-only.** Rejected always excluded. Pending opt-in only via an explicit `include_pending=true` query param + matching UI checkbox. Phase 3 Lite's dashboard answers "how much could we owe (worst case)?" so it includes pending; the accountant export answers "what is confirmed (defensible)?" so it does not. Different questions, different defaults.
> 2. **Per-job sheet header is date-range aware.** When `from_date` / `to_date` is set, "all-time" project numbers are NOT presented as if they match the rows on the sheet. The header is split into two clearly-labelled blocks: an **Export period totals** block (computed from the rows actually shown on the sheet, respecting the active filter + inclusion rule) and a **Project budget summary** block (always all-time, always Phase 3 Lite's reviewed+pending dashboard view, labelled clearly as such).
> 3. **Two new audit-friendly columns** on both All-Expenses and per-job sheets: **Raw input text** (`expenses.raw_input_text`) and **Created at** (`expenses.created_at`, ISO-UTC timestamp). Expense ID stays as the last column for cross-reference.

**Goal:** Generate a single `.xlsx` workbook on demand with:

1. an **`All Expenses`** sheet — every reviewed (default) or reviewed+pending (opt-in) expense across every job, one row per expense, BAS-friendly columns
2. one **per-job sheet** for every job that has at least one row in the export (after filters), with a two-block header (period totals + all-time project summary) plus the same column layout

The accountant downloads the file, opens it in Excel, and has everything they need to reconcile + lodge BAS without re-keying.

**Architecture:** Additive. **No schema changes. No new migrations.** One new backend dependency (`openpyxl`). One new service module + one new endpoint. One new admin button + hook. All data flows from existing Phase 1 / 2 / 3 Lite tables and services.

**Tech stack additions:** `openpyxl` (BSD-3-licence, the standard Python `.xlsx` writer; handles cell formatting, sheet styling, frozen headers).

---

## Reuse from earlier phases

| Earlier artefact | Path | Reused for |
|---|---|---|
| `expenses` table with all amount + status + supplier + category fields + raw_input_text + created_at | `backend/app/models/expense.py` (Phase 2) | Every expense row in every sheet |
| `jobs` table | `backend/app/models/job.py` (Phase 1) | Per-job sheet list + sheet headers |
| `summarize_jobs` / `summarize_job` services | `backend/app/services/budget_summary.py` (Phase 3 Lite, extended Lite+) | Per-job "Project budget summary" header block (always all-time) |
| `categories` / `suppliers` / `users` models | (Phase 1 / 2) | Joined display columns |
| `require_admin` dep | `backend/app/deps.py` (Phase 1) | Export is admin-only |
| Service → API thin-HTTP pattern | `backend/app/services/{jobs,expenses}.py` + `backend/app/api/{jobs,expenses}.py` | New `services/excel_export.py` + `api/reports.py` |

---

## Data model

**No schema change.** Pure read-only aggregation over Phase 1 + 2 + 3 Lite data.

### Inclusion rule (frozen, distinct from Phase 3 Lite)

| `expenses.review_status` | Default behaviour | With `include_pending=true` |
|---|---|---|
| `reviewed` | Included | Included |
| `pending` | **Excluded** | Included |
| `rejected` | Always excluded | Always excluded |

This is intentionally stricter than Phase 3 Lite's `(reviewed, pending)` rule. The dashboard's job is to surface worst-case ("how much could we owe?") so it counts pending; the accountant export's job is to hand over a defensible reconciliation set, so unreviewed entries stay out by default. The opt-in checkbox lets the user override when they want to see everything.

A clarifying note appears at the top of the All-Expenses sheet (row 2 below the title): `Inclusion rule: reviewed expenses only` OR `Inclusion rule: reviewed + pending` so the accountant knows what they're looking at.

### Date-range filter

Both `from_date` and `to_date` are optional and inclusive. The filter applies to `expenses.expense_date`, NOT `expenses.created_at` — the accountant cares about when the spend happened, not when SiteTracker recorded it.

---

## API surface

**One new route**, admin-only.

### `GET /reports/expenses-excel`

* **Auth:** `require_admin`
* **Query params** (all optional):
  * `from_date` — ISO date `YYYY-MM-DD`; inclusive lower bound on `expense_date`
  * `to_date` — ISO date; inclusive upper bound
  * `job_id` — UUID; restrict to a single job (if set, only that job's per-job sheet is generated, plus an All-Expenses sheet filtered to that job)
  * `include_pending` — boolean (`true` / `false`, default `false`); when `true`, includes `pending` rows alongside `reviewed`
* **Response:**
  * `200` with `Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`
  * `Content-Disposition` per the **Filename** section below (RFC 5987 dual `filename=` + `filename*=UTF-8''…` for safe non-ASCII, e.g. Chinese `晶晶`)
* **Error shapes:**
  * `400` if `from_date > to_date` or invalid date format
  * `401` / `403` per existing auth conventions
  * `404` if `job_id` doesn't resolve

---

## Workbook structure

### Sheet 1: `All Expenses`

**Top-of-sheet annotation rows** (rows 1–3, before the column headers on row 5):

| Row | Content |
|---|---|
| 1 | `All Expenses` (bold, larger font) |
| 2 | `Inclusion rule: reviewed expenses only` (or `reviewed + pending` when `include_pending=true`) |
| 3 | `Export period: {from_date or 'All time'} to {to_date or 'today'}` |
| 4 | (blank separator) |
| 5 | column header row (frozen via Excel pane freeze) |

**Columns** (17 total — was 15 in v1; +2 new audit columns):

| col | source | format |
|---|---|---|
| A — Date | `expenses.expense_date` | `DD/MM/YYYY` (Australian) |
| B — Job | `jobs.job_name` (joined) | text |
| C — Job code | `jobs.job_code` | text |
| D — Supplier | `suppliers.supplier_name` (joined; blank for labour/adjustment) | text |
| E — Category | `categories.category_name` (joined; blank for NULL) | text |
| F — Description | `expenses.description` | text |
| G — Amount inc GST | `expenses.amount_inc_gst` | `$#,##0.00` AUD |
| H — GST amount | `expenses.gst_amount` | `$#,##0.00` |
| I — Amount ex GST | `expenses.amount_ex_gst` | `$#,##0.00` |
| J — Payment method | `expenses.payment_method` | text (`cash` / `transfer` / `unknown`) |
| K — Receipt status | `expenses.receipt_status` | text (`no_receipt` / `expected_later`) |
| L — Review status | `expenses.review_status` | text (`reviewed` always; `pending` only when `include_pending=true`) |
| M — Entered by | `users.email` of `entered_by_user_id` | text |
| N — Notes | `expenses.notes` | text |
| **O — Raw input text** | `expenses.raw_input_text` | text — what the contributor originally typed (audit trail) |
| **P — Created at** | `expenses.created_at` | ISO-8601 UTC timestamp `YYYY-MM-DDTHH:MM:SSZ` (Australian-localised display deferred — UTC is unambiguous and the accountant rarely needs to know the second something was entered, so we don't bother with timezone conversion in V1) |
| Q — Expense ID | `expenses.expense_id` | text (uuid) — last column, narrow, for cross-reference |

**Footer row** (bold, on the row immediately below the last data row): `Totals` in A, sum of inc / GST / ex in G / H / I, blank elsewhere.

**Sort:** by Date ascending (oldest first — accountant's natural reading order for reconciliation).

**Date column** (A) and **Created at** column (P) are intentionally distinct: A is when the spend happened (matches bank statement); P is when it was recorded in SiteTracker (matches "did the user backfill this six weeks late?").

### Sheets 2..N: per-job sheets

One sheet per job that has at least one row in the export window (after the active inclusion + date filters). Sheet name = sanitised `job_name` (see "Sheet name sanitisation" below).

**Top-of-sheet header** (revised — split into two clearly-labelled blocks):

| Row | Content |
|---|---|
| 1 | `Job: {job_name}` (bold, larger) |
| 2 | `Job code: {job_code or '—'}`  ·  `Site: {site_address or '—'}` |
| 3 | (blank separator) |
| 4 | `Export period: {from_date or 'All time'} to {to_date or 'today'}` (italic muted) |
| 5 | `Inclusion rule: reviewed expenses only` (or `reviewed + pending`) (italic muted) |
| 6 | `Period totals (these rows): inc $X.XX  ·  GST $Y.YY  ·  ex $Z.ZZ` (bold) |
| 7 | (blank separator) |
| 8 | `Project budget summary (all-time, dashboard view — may differ from period totals above):` (italic muted) |
| 9 | `Contract value ex GST: $X.XX  ·  Total budget ex GST: $Y.YY` (or `—` for null) |
| 10 | `All-time spent inc GST: $X.XX  ·  ex GST: $Y.YY  ·  GST: $Z.ZZ` |
| 11 | `Remaining ex GST: $X.XX  ·  % consumed (all-time): Y.YY%` |
| 12 | (blank separator) |
| 13 | column header row (frozen) |

The **Period totals** block on row 6 is computed from the rows on the sheet — it always matches the columns G / H / I sum below. The **Project budget summary** block on rows 8–11 comes from `summarize_job` (Phase 3 Lite's all-time view, including pending) and is labelled explicitly so the accountant knows it's NOT a function of the export period — it's the dashboard view, included for context. The label "may differ from period totals above" makes the gap explicit.

**Columns** for per-job sheets (16 total — same 17 as All-Expenses minus column B "Job", since the entire sheet is one job):

A Date · B Job code · C Supplier · D Category · E Description · F inc GST · G GST · H ex GST · I Payment · J Receipt status · K Review status · L Entered by · M Notes · **N Raw input text** · **O Created at** · P Expense ID

**Footer:** `Totals` row beneath the last data row, sum of F / G / H. Matches row 6 of the header block (the period totals).

### Sheet name sanitisation

Excel forbids `\ / ? * [ ] :` in sheet names and limits to 31 chars. Algorithm:

1. Strip / replace forbidden chars with `_`
2. Truncate to 31 chars
3. If the resulting name starts with a dangerous formula prefix (`=`, `+`, `-`, `@`, `\t`, `\r`), prefix with an underscore so the tab name reads as plain text (defence-in-depth — sheet names don't evaluate formulas, but a tab labelled `=Bad Job` is still hostile UX and an injection-vector signal worth neutralising)
4. If two jobs collide on the truncated name, suffix `(1)`, `(2)`, …
5. Empty-after-strip → use first 8 chars of `job_id`

CJK chars (e.g. `晶晶`) are valid in Excel sheet names — they're preserved verbatim. Excel sheet name length is measured in chars, not bytes.

---

## Excel formula-injection protection (frozen)

**Threat model.** Any text value written into a cell is rendered by Excel / LibreOffice / Numbers verbatim on open. If the cell value starts with one of `=`, `+`, `-`, `@`, `\t` (tab), or `\r` (carriage return) — possibly after leading whitespace — the spreadsheet app interprets the cell contents as a **formula**. A contributor (or any parser path that round-trips raw text) could enter `=HYPERLINK("https://evil.example", "click")` or `=cmd|'/c calc'!A1` into a `description`, `notes`, or `raw_input_text` field, and when the accountant opens the workbook on their machine the formula runs (or at minimum displays a clickable link to an attacker-controlled URL). This is the canonical CSV/Excel injection vulnerability (CWE-1236; documented as a real-world attack vector by OWASP).

**Mitigation contract.** Every text cell goes through a single helper:

```python
def _safe_excel_text(value: str | None) -> str:
    """Return ``value`` with leading-formula-prefix neutralised.

    Spreadsheet apps treat a cell starting with =, +, -, @, \t, or \r
    (after leading whitespace) as a formula. We prepend an apostrophe
    (Excel's documented "treat as literal text" escape) so the value
    renders as inert text. The apostrophe is not displayed in the cell.

    None / empty → "" (no prefix).
    Control chars stripped except newline (legitimate multi-line notes).
    Strings that don't start with a dangerous prefix are returned verbatim
    (no apostrophe spam — only the at-risk values are altered).
    Strings already starting with a legitimate apostrophe are NOT
    double-quoted (only the formula prefixes get the escape).
    """
```

**Where it is applied** (every text cell in the workbook routes through this helper, no exceptions):

* **Per-row text columns** on All-Expenses + per-job sheets:
  * Job name (col B All-Expenses; per-job header row 1 title)
  * Job code (col C; per-job header row 2)
  * Supplier name (col D / per-job)
  * Category name (col E / per-job)
  * Description (col F / per-job)
  * Payment method (col J / per-job) — enum value, not user-controlled, but still routed
  * Receipt status (col K / per-job) — enum, defence-in-depth
  * Review status (col L / per-job) — enum, defence-in-depth
  * Entered-by email (col M / per-job)
  * Notes (col N / per-job)
  * **Raw input text** (col O / per-job) — **highest-risk vector** because it's literally what the contributor typed
  * Expense ID (col Q / per-job) — UUID is hex-only so will never trigger; routed for completeness
* **Header / annotation rows** on every sheet (sheet titles, `Inclusion rule:` labels, `Project budget summary:` labels, `Period totals:` labels, `Site:` line) — these are constants today but routing them through the helper costs nothing and prevents a future translator-string or template-injection slip from contaminating the workbook
* **Sheet names** — `_safe_sheet_name` already strips Excel-forbidden chars; step 3 of its algorithm now also neutralises a leading formula prefix (see **Sheet name sanitisation** above)
* **Filename** — `Content-Disposition` builder strips formula prefixes before writing the ASCII-fallback `filename=` form; the UTF-8 `filename*=` form is already percent-encoded so any prefix char is safely encoded as `%3D`, `%2B`, etc.

**What is NOT routed through the helper.** Numeric and date cells. They are passed to `openpyxl` as native `Decimal` / `date` / `datetime` values, not strings. The writer encodes them as numeric / date cell types so formula evaluation is impossible regardless of the value. Cell format strings (`$#,##0.00`, `DD/MM/YYYY`) are formatting properties, not cell values. Same for `Created at` (col P / per-job O) — written as a `datetime` object, not as the ISO string, so the timestamp display is governed by the cell number-format and no string-prefix path exists.

**Why apostrophe-prefix and not strip-the-prefix.** Stripping `=` from a description would silently mangle legitimate user content (e.g. an actual price-formula note like `"= $3.50/m × 12m"`). The apostrophe escape is reversible at copy-paste time, preserves the original meaning when the user / accountant re-reads the cell, and is the documented Excel convention for force-as-text.

---

### Filename

`Content-Disposition` uses RFC 5987 dual-form so non-ASCII job names (e.g. `晶晶`) survive the HTTP roundtrip:

```
Content-Disposition: attachment;
  filename="sitetracker-export-{ascii-fallback}-{stamp}.xlsx";
  filename*=UTF-8''sitetracker-export-{utf8-percent-encoded}-{stamp}.xlsx
```

* `{stamp}` rules:
  * If date-range filter set: `{from_date}-to-{to_date}` (e.g. `2025-07-01-to-2026-06-30`)
  * If single-job filter set: `{slugified-or-utf8-job-name}-{today}` (e.g. `daefdeef-2026-05-10` for the ASCII fallback when the job name is non-ASCII; `晶晶-2026-05-10` in the UTF-8 form)
  * Otherwise: `{today-iso-date}` (e.g. `2026-05-10`)
* The ASCII fallback for `filename=` strips non-ASCII chars and replaces with the first 8 chars of `job_id` if the slug ends up empty. Modern browsers (Chrome / Firefox / Safari / Edge) all honour `filename*=UTF-8''…` and present the UTF-8 form to the user; older clients fall back to the ASCII version. **No browser breaks.**

---

## Service / module layout

### Backend

* `backend/app/services/excel_export.py` — new
  * `build_workbook(db, *, from_date=None, to_date=None, job_id=None, include_pending=False) -> bytes`
  * `_build_all_expenses_sheet(workbook, expenses, *, inclusion_label, period_label)` — internal
  * `_build_job_sheet(workbook, job, expenses, summary, *, inclusion_label, period_label)` — internal
  * `_safe_sheet_name(name, used_names)` — Excel-name sanitiser; also neutralises leading formula prefix
  * `_safe_excel_text(value)` — single source of truth for formula-injection neutralisation; called for **every text cell** in the workbook (see Excel formula-injection protection above)
  * `_safe_filename(*, ascii_fallback, utf8)` — RFC 5987 Content-Disposition builder; strips formula prefixes from the ASCII-fallback form
* `backend/app/api/reports.py` — new, mounted at `/reports`
  * `get_expenses_excel_endpoint` returns `StreamingResponse` (FastAPI streaming)
* `backend/app/api/router.py` — extend to include `reports.router`
* `backend/pyproject.toml` — add `openpyxl >= 3.1`

### Frontend

* `admin/src/api/hooks/useExpensesExcel.ts` — new
  * Triggers a download via authenticated `fetch` + Blob URL (so the JWT goes in the request header; can't use a plain `<a href>` — that loses auth headers)
  * Reads the `Content-Disposition` from the response to derive the saved filename (parses the `filename*=UTF-8''…` form for non-ASCII job names)
* `admin/src/pages/Expenses.tsx` — add download trigger
  * Two `<input type="date">` fields (from / to)
  * One `<input type="checkbox">` labelled "Include pending / unreviewed entries"
  * `Download Excel` button

---

## Test strategy

### Backend unit tests (`tests/test_excel_export.py`)

Each test reads back the workbook bytes via `openpyxl.load_workbook(BytesIO(bytes))` and asserts on cell values, sheet names, count, and the period-totals math.

* **Empty input** — workbook has only the `All Expenses` sheet with header, annotation rows, and zero-totals footer
* **One reviewed supplier expense** — one row in All Expenses + one per-job sheet exists with header block + summary block + the row + footer totals matching the row
* **Pending excluded by default** — adding a pending row does NOT appear in the workbook when `include_pending=False`
* **`include_pending=True` includes pending** — same row from above DOES appear with `pending` in column L; period totals reflect it
* **Rejected excluded always** — a rejected row never appears, regardless of `include_pending`
* **Mixed cash + transfer** — column H (GST) shows 0 for cash, standard 1/11 for transfer; totals row sums correctly
* **Labour expense (no supplier)** — Supplier column is blank, not `None`
* **Expense with NULL category** — Category column blank
* **Date-range filter** — expenses outside the range are dropped from both All-Expenses and per-job sheets; per-job header row 6 ("Period totals") matches the filtered sum, NOT the all-time spend
* **Per-job header date-range awareness** — when filtered, row 6 (Period totals) and rows 8-11 (Project budget summary) show DIFFERENT numbers and are labelled correctly
* **Per-job header WITHOUT filter** — row 6 (Period totals) and rows 8-11 (Project budget summary) may agree on most numbers but the label is still "all-time" so accountant intent is unambiguous; if `include_pending=False` AND there are pending expenses, the period totals (reviewed-only) will differ from the dashboard project summary (reviewed+pending) — this should be visible and labelled
* **`job_id` filter** — only that job's per-job sheet exists; All-Expenses contains only that job's rows
* **Sheet name sanitisation** — names with `/`, `\`, `:`, `*`, `?`, `[`, `]` get cleaned; collisions get `(1)` suffix; long names truncated at 31 chars; **CJK-only names (`晶晶`) preserved verbatim**
* **Filename safety** — when `job_id` is the `daefdeef-…` 晶晶 job, `Content-Disposition` carries both `filename="sitetracker-export-daefdeef-2026-05-10.xlsx"` AND `filename*=UTF-8''sitetracker-export-%E6%99%B6%E6%99%B6-2026-05-10.xlsx`
* **Decimal precision** — money cells contain Decimal values (no float drift); period totals match per-row sums
* **Audit columns present** — column O (raw_input_text) and column P (created_at, ISO-UTC) populated correctly; created_at format matches `^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}` (with optional `Z` or `+00:00`)
* **Empty raw_input_text** — for structured-entered expenses where `raw_input_text` is NULL, column O is blank (not `None`)

#### Excel formula-injection neutralisation (frozen contract — Batch 1 backend test scope)

Each test creates an expense with a hostile string in the named field, builds the workbook via `build_workbook`, reads the cell back via `openpyxl.load_workbook(BytesIO(bytes))`, and asserts on the cell's stored value. Apostrophe-prefixed values are detected by checking that `cell.value.startswith("'")` and the rest equals the input (openpyxl preserves the apostrophe in the cell value; Excel hides it on display).

* `test_excel_injection_equals_in_description_neutralised` — `description="=HYPERLINK(\"https://evil.example\",\"click\")"`; col F cell value = `"'=HYPERLINK(...)"` (leading apostrophe present)
* `test_excel_injection_plus_neutralised` — `description="+SUM(A1:A10)"`; col F starts with `'+`
* `test_excel_injection_minus_neutralised` — `description="-2+3"`; col F starts with `'-`
* `test_excel_injection_at_neutralised` — `description="@SUM(A:A)"`; col F starts with `'@`
* `test_excel_injection_tab_neutralised` — `description="\tcalc()"`; col F starts with `'\t`
* `test_excel_injection_cr_neutralised` — `description="\r=evil"`; col F starts with `'\r`
* `test_excel_injection_in_notes_field` — same `=HYPERLINK(...)` payload in `notes`; col N stays inert
* `test_excel_injection_in_raw_input_text_field` — same payload in `raw_input_text`; col O stays inert (the **highest-risk vector** because raw_input_text is literally what the contributor typed)
* `test_excel_injection_in_supplier_name` — `supplier_name="=cmd|/c calc"`; col D stays inert on All-Expenses AND the per-job sheet; supplier-name display in any future header row (none in V1) is also routed
* `test_excel_injection_in_category_name` — `category_name="=Bad Cat"`; col E stays inert
* `test_excel_injection_in_job_name` — `job_name="=Bad Job"`; col B (All-Expenses) AND the per-job sheet's row-1 title block both render the value with the apostrophe prefix
* `test_excel_injection_in_job_name_sheet_name` — same `=Bad Job` job_name; the sheet TAB name does NOT start with `=` (the sheet-name sanitiser's step-3 underscore-prefix kicked in); the tab reads `_=Bad Job` (visually showing the leading `=` is harmless inside a sheet name; the underscore prefix is the signal that injection was detected and neutralised)
* `test_excel_injection_in_entered_by_email` — `users.email="=evil@example.com"` (synthetic — emails normally don't start with `=`, but defence-in-depth); col M stays inert
* `test_excel_safe_text_passes_through_normal_strings` — `description="bunnings cement bag 20kg"` → col F value is exactly `"bunnings cement bag 20kg"` (no apostrophe added)
* `test_excel_safe_text_passes_through_strings_with_dangerous_char_mid_string` — `description="3 × 12m = $36"` (the `=` is not the FIRST char) → col F value preserved verbatim; not neutralised (mid-string `=` is legitimate user content)
* `test_excel_safe_text_handles_none_and_empty` — expense with `description=None` and one with `description=""`; both cells render as `""` (no apostrophe spam)
* `test_excel_safe_text_strips_control_chars` — `notes="legit text\x00with null byte"`; null byte stripped, surrounding text preserved; no apostrophe added (no leading dangerous char)
* `test_excel_safe_text_preserves_legitimate_leading_apostrophe` — `notes="'tis the season"` (legitimate leading apostrophe, e.g. a quote) → col N value is `"'tis the season"` with EXACTLY one apostrophe (no doubling); the helper does NOT prepend a second apostrophe just because the value already has one
* `test_excel_safe_text_handles_leading_whitespace_then_dangerous_char` — `description="   =evil"` → col F starts with `'   =evil` (the leading whitespace is preserved verbatim; the trim-check still detects the danger after stripping leading whitespace and prepends the apostrophe to the original value, not to a trimmed version)
* `test_excel_injection_in_annotation_rows_routed_through_helper` — annotation strings (`Inclusion rule: …`, `Project budget summary …`, `Period totals: …`) are constants today but the helper is invoked on them; this test asserts the call path by patching `_safe_excel_text` and asserting it's called for each annotation row's text content (i.e. proves the helper is in the write path even for trusted strings, so future changes can't accidentally bypass it)

### Backend integration tests (`tests/test_reports_api.py`)

* `GET /reports/expenses-excel` happy path returns 200 + correct `Content-Type` + valid `Content-Disposition`
* Body is a valid `.xlsx` (load via openpyxl, check expected sheets present + annotation rows correct)
* `?include_pending=true` reflects in the All-Expenses annotation row 2
* `?from_date=...&to_date=...` reflects in row 3 + per-job header row 4
* 401 with no token
* 403 for contributor token
* 400 on `from_date > to_date`
* 400 on malformed date string
* 404 on unknown `job_id`
* Filename respects the date-range / single-job / today-stamp rule per the filename spec

### Frontend

Manual E2E via Claude Preview:

* Default download (no filters, `include_pending=false`) → file opens with reviewed-only rows
* Toggle "Include pending / unreviewed entries" checkbox → file includes pending rows + annotation row 2 reflects the new rule
* Set date range → file rows narrow + per-job header row 6 (Period totals) matches the filtered sum
* Authenticated request honoured (JWT via fetch headers; download via Blob URL)
* Filename in the saved file matches the `filename*=UTF-8''…` form on browsers that honour RFC 5987 (Chrome, Firefox, Safari, Edge — all do)

### Regression

`pytest -q` → target ~565 (525 current + ~20 functional + ~20 injection-neutralisation = ~40 new). Admin `npx tsc --noEmit` + `npm run build` clean. Mobile typecheck unchanged.

---

## Out of scope (deferred)

* **Per-category subtotals on per-job sheets** — could be added later; for V1, row-level data + bottom totals is enough
* **Conditional formatting** (red over-budget rows, etc.) — out for V1
* **CSV / Google Sheets / PDF** — out
* **Scheduled / emailed export** — out
* **Inclusion of attachments / receipt files** — Phase 5 surface; V1 only carries the `Receipt status` column
* **Labour attendance breakdown** — Phase 5
* **Multi-currency** — V1 is AUD-only
* **i18n / Chinese sheet headers** — accountant-facing; English-only for V1 (Chinese-named jobs work, but column headers + annotation text stay English)
* **Australian-local timestamp formatting on Created at** — V1 ships ISO-UTC; localisation deferred

---

## Batches (2)

### Batch 1 — Backend: dependency + service + endpoint + tests

1. **T-A: Dependency.** Add `openpyxl` to `backend/pyproject.toml` + `uv sync`.
2. **T-B: Service.** New `backend/app/services/excel_export.py` with `build_workbook` + the four internal helpers (sheet builders, name sanitiser, filename builder).
3. **T-C: Endpoint.** New `backend/app/api/reports.py` exposing `GET /reports/expenses-excel`. Streams via `StreamingResponse(BytesIO(bytes), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": ...})`.
4. **T-D: Router wire-up.** Mount `reports.router` on `app.api.router`.
5. **T-E: Backend tests.** Unit + integration per the test strategy.

**Batch-1 exit:** `pytest -v` green including all ~40 new tests (functional + injection-neutralisation); sample curl + xlsx open in Excel produces the expected workbook against the live `daefdeef-…` 晶晶 data; injection-neutralisation tests prove every text-cell write path routes through `_safe_excel_text`.

### Batch 2 — Admin: download trigger + filters + manual E2E + commit

6. **T-F: TS types regen** for completeness (the new endpoint returns binary, not JSON, but the OpenAPI surface change is worth re-generating from).
7. **T-G: Hook.** `admin/src/api/hooks/useExpensesExcel.ts` — authenticated fetch + Blob URL + Content-Disposition filename parse.
8. **T-H: Page wiring.** Date-range inputs + "Include pending" checkbox + "Download Excel" button on `/expenses`.
9. **T-I: i18n.** ~5 new EN-only keys (English-only export per scope). No zh.json change.
10. **T-J: Manual E2E.** Verify against live 晶晶 data — pure read, no data writes.
11. **T-K: Regression gate** + two commits matching the Phase 3 Lite split convention (backend, then admin).

**Batch-2 exit:** Admin user downloads a valid `.xlsx` from `/expenses`, with date filter + include-pending checkbox both round-tripping correctly; the file opens cleanly in Excel and the per-job sheet for 晶晶 carries the correct two-block header.

---

## Critical files

### New this phase

**Backend:**
* `backend/app/services/excel_export.py`
* `backend/app/api/reports.py`
* `backend/tests/test_excel_export.py`
* `backend/tests/test_reports_api.py`

**Frontend:**
* `admin/src/api/hooks/useExpensesExcel.ts`

### Modified

* `backend/pyproject.toml` — add `openpyxl`
* `backend/app/api/router.py` — mount `reports.router`
* `admin/src/pages/Expenses.tsx` — add download trigger + filters
* `admin/src/api/types.ts` + `mobile/src/api/types.ts` — regenerated
* `admin/src/i18n/en.json` — ~5 new keys (button label, "Include pending" checkbox label, date-from / date-to labels, error toast)

### Not modified

* All Phase 1 / 2 / 3 Lite + Lite+ + Lite++ models, schemas, services, migrations
* Mobile Expo source
* `admin/src/i18n/zh.json` — accountant export is English-only by scope decision

---

## Confirmed defaults (from your review)

| Item | Value |
|---|---|
| Default inclusion rule | `reviewed` only; `pending` opt-in via `include_pending=true` |
| Rejected expenses | Always excluded |
| Pending opt-in UI | "Include pending / unreviewed entries" checkbox beside the date inputs |
| Date-range filter | Optional `from_date` + `to_date` query params; two `<input type="date">` fields beside the Download button |
| Per-job sheet for empty jobs | No (only jobs with at least one row in the export window) |
| Per-job sheet sort order | Alphabetical by `job_name` |
| Per-job header structure | Two blocks: "Period totals (these rows)" + "Project budget summary (all-time, dashboard view — may differ from period totals above)" |
| New audit columns | Raw input text (col O / per-job N), Created at ISO-UTC (col P / per-job O) |
| Final cross-reference column | Expense ID (col Q / per-job P) |
| Filename pattern | `sitetracker-export-{date-range-or-job-or-today}.xlsx`, RFC 5987 dual-form for non-ASCII job names (`晶晶` survives) |
| Endpoint path | `GET /reports/expenses-excel` |
| Admin trigger | Button on existing `/expenses` page |
| Auth | `require_admin` |

If you confirm this plan, I'll start Batch 1 (backend) and stop at the Batch 1 boundary for explicit approval before Batch 2.

---

## Why this stays small

Phase 4 is a thin read-only export over data Phases 1–3 already collect. No new tables, no new enums, no migrations, no parser / review / dashboard changes. The two operator-review revisions (stricter default inclusion + date-range-aware per-job header) make the export safer to hand to an accountant without changing the underlying data shape. The two new audit columns surface fields that already exist on `expenses`. Test scope is bounded to the new service + endpoint. Frontend touches one page. The build is small because the request is small.
