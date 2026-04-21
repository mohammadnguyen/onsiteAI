# SiteTracker V1 — Product Spec

> **Status:** source of truth for V1 scope. Derived from the original product
> brief agreed with the user. Phase-by-phase implementation plans (beginning
> with `docs/phase-1-plan.md`, added in a later task) break this down into
> executable work.

## Product positioning

SiteTracker V1 is an **internal cost-control and expense-tracking app** for a
small residential builder. It is deliberately not a scheduling tool, not a CRM,
and not a client-facing portal. The target user is the builder-owner and a
small team of site contributors who need to:

- **Capture costs in seconds** from the job site — ideally one line of free
  text per expense — without navigating forms or dropdowns.
- **See, per job:** contract value vs. total cost vs. reviewed-vs-pending cost,
  with an **estimated margin** rolled up from live data.
- **Export to Excel** for the accountant at the end of each month or job.
- **Work in English and Simplified Chinese (zh-CN)** interchangeably.
- **Support multiple users** with a simple two-role model — an admin who
  configures jobs and users, and contributors who log expenses and labour.

The app is optimized for speed of capture on a phone in the field. Everything
else (reports, export, admin) is secondary to that primary flow.

## V1 core modules

### 1. Expense capture

A single text input on the mobile app. A contributor types something like:

```
$305 Bunnings Kelly bluemetal
```

or, in Chinese:

```
工地1 水工材料 163
```

The app parses the message using a **hybrid parser** (deterministic rules for
amount, supplier, and job alias; LLM-assisted fallback for ambiguous cases) and
fills in **amount, supplier, job, category, and note**. High-confidence
parses save directly; low-confidence parses land in a **review queue** for a
human to confirm before they hit the ledger.

Examples (from the original brief):

| Input                             | Parsed                                                                  |
| --------------------------------- | ----------------------------------------------------------------------- |
| `$305 Bunnings Kelly bluemetal`   | $305, supplier Bunnings, job "Kelly", category Landscaping/Earthworks.  |
| `工地1 水工材料 163`               | ¥163 (or $163 local), job alias "工地1", category Plumbing, note 水工材料. |

Captures are always attributed to the signed-in user and time-stamped.

### 2. Jobs / sites

Each job (also referred to as a "site") has:

- A human name (e.g. `123 Kelly St — Smith reno`).
- An optional **contract value** (for margin calculation).
- An optional **total budget** (may equal, or differ from, contract value).
- **Aliases** — short strings the parser matches against free-text input
  (e.g. `Kelly`, `工地1`, `Smith`). Multiple aliases per job are supported.
- **Per-category budgets** — optional line-item budgets keyed to the 23 builder
  categories (see below) for finer-grained tracking.
- A status (active / archived).

Jobs are the primary organising unit. Every expense and every labour record
belongs to exactly one job.

### 3. Dashboard

Per job (and roll-up across all active jobs), the dashboard shows:

- **Contract value** (if set).
- **Total budget** (sum of per-category budgets or a single job-level number).
- **Total cost to date** — reviewed expenses only.
- **Reviewed vs pending** cost — pending = in the review queue.
- **Estimated margin** — `contract_value - total_cost_reviewed` (with the
  pending pool shown separately so the user knows the uncertainty band).
- **Top 5 suppliers** by spend.
- **Top 5 categories** by spend.
- **Monthly trend** — a simple month-by-month stacked or grouped bar chart of
  spend per category.

### 4. Labour attendance

Tracked **separately from expenses** — a contributor logs "was at site X on
day Y for Z hours" or similar. Labour does not flow into the expense ledger in
V1; it is reported independently. (Cost impact modelling is out of scope for
V1.)

### 5. Categories (23 builder categories, verbatim)

These are the fixed V1 category list. They are seeded once and referenced by
ID everywhere.

1.  Demolition
2.  Earthworks
3.  Concrete
4.  Brickwork
5.  Carpentry
6.  Roofing
7.  Cladding
8.  Waterproofing
9.  Plumbing
10. Electrical
11. Gyprock
12. Painting
13. Flooring
14. Tiling
15. Joinery
16. Windows & Doors
17. Structural Steel
18. Labour
19. Preliminaries
20. Equipment Hire
21. Waste / Skip Bin
22. Delivery
23. Miscellaneous

### 6. Roles

Two roles in V1:

- **Admin** — creates and edits jobs, manages aliases and per-category
  budgets, invites users, changes user roles, edits or deletes any expense or
  labour record, performs review-queue decisions, runs Excel exports.
- **Contributor** — logs expenses and labour for jobs they are assigned to (or
  all active jobs — simple access model in V1), views their own entries, sees
  the dashboard read-only.

(Finer-grained permissions — per-job contributor access, approver chains, etc.
— are out of scope for V1.)

### 7. Excel export

Single-click export of all expenses for a chosen date range, producing an
`.xlsx` with:

- **`All Expenses` sheet** — flat list of every expense across every job,
  sortable by date / job / supplier / category.
- **One sheet per job** — the same columns, filtered to that job, with a small
  summary header (contract value, total cost, estimated margin).

Labour attendance exports separately (format TBD — likely a second workbook or
additional sheet). The Excel file is what the builder hands to the accountant.

### 8. Language support

Two languages:

- **English** (default).
- **Simplified Chinese (zh-CN).**

Language is selectable per user; all user-facing strings, category labels, and
parser behaviour (tokens like `工地1`, `水工材料`) work in both.

## Technical direction

- **Mobile:** React Native + Expo. iOS first, Android parity as a stretch.
  Expo Go for dev, TestFlight for closed beta.
- **Backend:** FastAPI (Python 3.12) with Postgres 16. SQLAlchemy 2 async,
  Alembic migrations, Pydantic v2. JWT auth (access + refresh).
- **Admin web:** Vite + React + TypeScript. Shares OpenAPI-generated types
  with the backend. Lightweight UI — admin is a power-user surface, not a
  broad-audience product.
- **Infrastructure:** Docker Compose for local dev; production hosting decided
  per-phase (Phase 6).

## Out of scope for V1

Explicitly deferred to post-V1 (may or may not ever ship):

- Construction scheduling / Gantt / trade sequencing.
- CRM (lead management, customer pipeline).
- Client portal (end-customer access to job progress).
- Subcontractor portal (sub logging in to submit invoices, etc.).
- Xero / QuickBooks / MYOB integration.
- Purchase orders (PO issue, matching, three-way).
- Variation / change-order workflow.
- Cashflow forecasting.
- WIP (work-in-progress) accounting.
- Payroll.
- Push notifications (for review-queue prompts, budget alarms, etc.).
- Multi-company / multi-tenant SaaS. V1 is single-company, deployed for one
  builder.

## V1 phase order

1. **Phase 1 — Auth + jobs + users.** Monorepo scaffold, backend with auth /
   users / jobs / categories, thin mobile shell (login + jobs list), thin
   admin shell (login + jobs CRUD + invites). Everything else builds on this.
2. **Phase 2 — Expense entry + review queue.** Expense model, hybrid NL
   parser (rules + LLM fallback), review queue UI on mobile and admin.
3. **Phase 3 — Dashboard.** Job list cards, job detail, top-5 suppliers and
   categories, monthly trend, estimated margin.
4. **Phase 4 — Excel export.** All Expenses + per-job sheets, downloadable
   from admin.
5. **Phase 5 — Attachments + bilingual polish.** Receipt upload (S3-compatible
   storage, OCR-ready but not required), labour attendance UI, final EN/zh-CN
   QA pass.
6. **Phase 6 — TestFlight.** iOS TestFlight build, production backend
   deployment, bilingual QA on real devices, admin deployed behind auth.

## Implementation plans

The Phase 1 implementation plan lives at `docs/phase-1-plan.md`. It is added
in a later Task in the Phase 1 build (not created as part of the initial
scaffold). Later phases each get their own plan file as work on them begins.
