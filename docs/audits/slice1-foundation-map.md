# Slice-1 Foundation Map

**Purpose:** Session-1 repo audit required by README-HANDOFF / ADR-002 execution order step 1.
**Buckets (DEC-EXISTING-001):** `slice-1-foundation` | `existing-maintain` | `untouched-for-now` | `conflicts-with-decision`
**Date:** 2026-08-10. Audited tree: `main` @ b636317.

Bucket meanings:

- **slice-1-foundation** — infrastructure or domain surface the Capture → Evidence → Candidate → Confirmation → Truth loop will build on directly.
- **existing-maintain** — shipped, user-relied surface in Charter DIRECTION / NOT NOW territory. Light-gate maintenance only; no extension without promotion through PRODUCT.md; never remove capability by implication.
- **untouched-for-now** — not needed by Slice 1, not under active maintenance pressure. Leave alone.
- **conflicts-with-decision** — violates a DEC-* decision; requires adjudication.

---

## Backend (`backend/app`)

| Module | Bucket | Notes |
|---|---|---|
| `api/auth.py`, `services/auth.py`, `core/security.py`, `models/user.py`, `api/users.py`, `services/users.py` | slice-1-foundation | Identity, roles (admin/contributor), rate-limited login. Slice-1 project members ride on this. |
| `api/jobs.py`, `services/jobs.py`, `models/job.py`, `models/job_audit_log.py` | slice-1-foundation | Jobs are an explicit Slice-1 foundation (PRODUCT.md §1). Job audit log is the in-repo precedent for DEC-SITELOG-META-001-style audit history. |
| `core/`, `config.py`, `database.py`, `deps.py`, `main.py` | slice-1-foundation | App infra: async SQLAlchemy, soft-delete filter, config validation. Reused as-is. |
| `api/expenses.py`, `services/expenses.py`, `services/expense_write.py`, `models/expense.py` | existing-maintain | Expense capture/write path. Cost control is Charter DIRECTION → not extendable without promotion. |
| `api/categories.py`, `models/category.py`, `api/suppliers.py`, `services/suppliers.py`, `models/supplier.py` | existing-maintain | Expense-domain reference data. Supplier context may later inform Slice-1 context injection (DEC-PERSONALIZATION-001) — that reuse is a promotion decision, not maintenance. |
| `api/labour.py`, `services/labour.py`, `models/labour.py` | existing-maintain | Labour attendance/rollups. DIRECTION territory (cost views). |
| `services/parser/*` (orchestrator, amount, dates, jobs, suppliers, categories, cjk_amounts, llm_adapter, review, duplicates…) | existing-maintain | Deterministic expense parser + LLM adapter + confidence gating. This is NOT the Slice-1 extraction pipeline; it is expense-domain. Its patterns (confidence reason codes, threshold table, review gating, no-raw-text logging) are precedents for Slice-1 extraction design. |
| `api/review_queue.py`, `services/review_queue.py`, `models/review_queue.py` | existing-maintain | Expense review queue. Pattern precedent for Slice-1 confirmation UX (exception-based review), but Slice-1 candidates/confirmations are a new schema per DEC-TRUTH-001/DEC-EVIDENCE-001, not a reuse of this table. |
| `api/reports.py`, `services/budget_summary.py`, `services/excel_export.py` | existing-maintain | Budget/margin/GST reporting + Excel export. DIRECTION territory. |
| Evidence / object storage | **ABSENT** | See DEC-EVIDENCE-001 determination below. |

## Mobile (`mobile`)

| Module | Bucket | Notes |
|---|---|---|
| `app/(auth)/*`, `src/store/auth.ts`, `src/store/session.ts`, `src/api/*` | slice-1-foundation | Login, session, API client/query layer. |
| `app/(tabs)/jobs.tsx`, `src/components/JobPickerSheet.tsx`, `NewJobModal.tsx`, `src/util/jobStatus.ts` | slice-1-foundation | Job list/selection UX — the substrate for explicit Job attribution (DEC-JOB-ATTR-001, ~one tap). |
| `src/ui/*` (kit, tokens, icons, AppTabBar, type), `src/i18n/*`, `src/util/*` | slice-1-foundation | Shared UI kit + i18n + utils. |
| `app/capture.tsx`, `src/components/capture/*`, `CaptureResultCard.tsx`, `RecentCapturesList.tsx`, `RecentFailuresList.tsx`, `src/store/failures.ts` | existing-maintain | Expense capture flow. Adjacent to Slice-1 Capture but domain-specific; Slice-1 capture (voice/text/photo → Site Log) is new build, planned against PRODUCT.md, not an extension of this screen "by side effect". |
| `app/expenses/*`, `src/components/ExpenseRow.tsx`, `src/store/expenseListFilters.ts` | existing-maintain | Expense list/detail. |
| `app/(tabs)/labour.tsx`, `src/components/labour/*`, `WorkerChecklist.tsx`, `src/store/labourEditTarget.ts` | existing-maintain | Labour screens. |
| `app/(tabs)/home.tsx`, `src/components/home/*` | existing-maintain | Dashboard (money strip, review stack, attendance). |
| `app/review-queue.tsx`, `ReviewCorrectionsSheet.tsx` | existing-maintain | Expense review queue UI. |
| `app/export.tsx`, `app/settings.tsx`, `app/users/*` | existing-maintain | Export, settings, user admin. |

## Other

| Module | Bucket | Notes |
|---|---|---|
| `admin/` (web) | untouched-for-now | Secondary surface; manually rebuilt from main when needed, no deploy pipeline. Not needed by Slice 1. |
| `backend/scripts/` (seed_admin, reset_testing_expenses, trial_telemetry_report) | untouched-for-now | Ops utilities. |
| `docs/design/ui-kit-v2`, phase-plan docs | untouched-for-now | Historical/reference. |

## conflicts-with-decision

**None found.** Specifically checked the AI boundary (DEC-AI-BOUNDARY-001 / DEC-TRUTH-001): the expense parse path (`services/parser/orchestrator.py`) produces a parse preview; an expense row is written only on an explicit user save, and low-confidence parses are additionally gated into the review queue (`review_status=pending` via frozen threshold table in `parser/review.py`). No code path lets model output write records without a human action. Caveat: this is an audit-level pass, not a full-gate boundary review; Slice-1 extraction design gets its own plan-review.

---

## DEC-EVIDENCE-001 determination

**Status: OPEN — no existing implementation, PR, or branch satisfies it.**

Evidence (all checked 2026-08-10 against `origin`):

1. Working tree: no object-storage code in `backend/` (no S3/Tigris/boto client, no blob/attachment table; only textual `raw_input_text` on Expense). Grep for `s3|tigris|boto|storage|upload|photo` hits only comments and expense fields.
2. Remote PRs: `git ls-remote origin 'refs/pull/*/head'` returns PRs 1–3 only. **PR 6 does not exist on this remote** — the "PR 6, Tigris storage" item from the strategy phase does not correspond to anything on `mohammadnguyen/onsiteAI`.
3. Remote branches: all 37 heads listed; none match storage/tigris/evidence/upload naming.

**Consequence (per README-HANDOFF step 1):** a dedicated **Evidence Storage foundation slice** must be planned and landed as its own gated PR before Slice-1 implementation. Binding requirement is retention (DEC-EVIDENCE-001): raw evidence stored, never destroyed by extraction; confirmation-delta schema from the first migration that touches it. Storage provider (Tigris / other S3-compatible / alternative) is an implementation choice made in that slice's plan-review against repo, deployment environment (Fly.io) and cost — not pre-decided here.
