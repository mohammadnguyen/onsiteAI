# Phase 2 internal trial — official baseline

This doc captures the state of the dev DB at the moment the trial began. It exists so the post-trial counting process can unambiguously separate **real trial data** from **Claude E2E noise** left over from Batch 4–related verification work. It is intentionally immutable — do not update it as the trial progresses; the issue log in `internal-testing.md` is the live surface.

---

## Trial start timestamp

**`2026-04-24 09:31:17 UTC`** — the `created_at` of the first admin-entered expense on the 晶晶 job (row 1 below).

Any expense, review-queue row, or audit row with `created_at >= 2026-04-24 09:31:17 UTC` is in scope for trial interpretation, subject to the row-counting rules below.

---

## Counting rules

Applied when tallying post-trial counts and interpreting log rows.

1. **Real trial data — count in business interpretation:** the 4 expenses on the `晶晶` job entered by `admin@example.com` (rows 1, 5, 6, 7 below).
2. **Claude E2E noise — exclude from business interpretation:** the 3 expenses on the `Kelly House` job entered by `jeffrey@example.com` (rows 2, 3, 4 below). These were submitted during the payment-picker + cash-GST-rule E2E verification work and do not reflect real builder usage.
3. **Rejected entries still count:** rejection is a real review-queue outcome. A duplicate that was correctly rejected, or an entry the admin triaged as unwanted, is valid trial evidence — it exercises the review / duplicate workflow even though the row ends up with `review_status = rejected`.

Rule 2 applies to the three Kelly-House rows specifically. Any future rows entered by any user on any job after the trial-start timestamp are real trial data, including future Jeffrey entries.

---

## t=0 snapshot — the 7 expenses at trial start

| # | created_at (UTC) | amount | raw_input_text | job | supplier | status | payment | entered_by | classification |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 2026-04-24 09:31:17 | 8000.00 | `拆除分包 $8000 晶晶` | 晶晶 | (none) | rejected | unknown | admin@example.com | **real trial — count** |
| 2 | 2026-04-24 10:02:14 | 500.00 | `bunnings $500 现金 Kelly bluemetal` | Kelly House | Bunnings | reviewed | cash | jeffrey@example.com | Claude E2E — **exclude** |
| 3 | 2026-04-24 10:02:37 | 1000.00 | `水泥 $1000 转账 Kelly` | Kelly House | (none) | rejected | transfer | jeffrey@example.com | Claude E2E — **exclude** |
| 4 | 2026-04-24 10:03:11 | 200.00 | `$200 Bunnings Kelly timber` | Kelly House | Bunnings | reviewed | cash | jeffrey@example.com | Claude E2E — **exclude** |
| 5 | 2026-04-24 10:16:08 | 950.00 | `垃圾桶 $950 现金 晶晶` | 晶晶 | (none) | reviewed | cash | admin@example.com | **real trial — count** |
| 6 | 2026-04-24 10:17:14 | 8000.00 | `拆除 $8000 现金 晶晶` | 晶晶 | (none) | reviewed | cash | admin@example.com | **real trial — count** |
| 7 | 2026-04-24 10:27:33 | 8000.00 | `拆除 $8000 现金 晶晶` | 晶晶 | (none) | rejected | cash | admin@example.com | **real trial — count** (duplicate-rejected; row 6 is the kept original) |

**Counted rows at t=0:** 4 (rows 1, 5, 6, 7).

**Excluded rows at t=0:** 3 (rows 2, 3, 4).

---

## Associated non-expense state at t=0

Preserved — these are baseline setup, not trial data, and are not subject to the counting rules:

- Users: 2 (`admin@example.com`, `jeffrey@example.com`)
- Jobs: 2 (`Kelly House` — Phase-1 seed; `晶晶` — created by the user before the trial opened)
- Job aliases: 3 (`Kelly` en, `工地１` zh, `晶晶家` on the 晶晶 job)
- Suppliers: 1 (`Bunnings`) + 1 alias (`bunnings`)
- Categories: 23 (Phase-1 seed)
- Review queue: 5 open/closed rows
- Audit log: 5 rows

The 晶晶 job and its alias were added through the admin UI before the trial opened and are real infrastructure for the trial, not test junk.

---

## How this doc is used

- **During the trial:** ignored. Do not update it as the trial progresses. The live surface is the issue log (`internal-testing.md` → Issue log template).
- **After the trial closes:** read it alongside the log to apply the counting rules when producing the six-number tag totals. Rows added to `expenses` after the trial start timestamp count per their entered_by + job, not per a global exclusion list — only rows 2, 3, 4 above are excluded, and only because they were created before the trial-start timestamp by Claude E2E work.

Framework source of truth remains [`internal-testing.md` → Post-trial decision framework](internal-testing.md#post-trial-decision-framework).
