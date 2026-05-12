# 0001 — Current Architecture

## Status

Accepted (2026-05-13).

## Context

SiteTracker (internally referred to as Budget AI in `CLAUDE.md`) is an
internal cost-control product for a small NSW residential builder. The
constraints that shaped the stack:

- One small operating team (one builder on site, one office admin, one
  accountant). No multi-tenant requirements.
- iOS-first: the builder logs expenses from a phone on a job site;
  weak network, distracted attention, one-handed use, short sessions.
- AU GST: every amount has an inclusive form, an exclusive form, and a
  GST component. Cash payments are GST-exclusive by rule.
- Bilingual: every visible string exists in English and Simplified
  Chinese.
- Capture is natural-language-first ("Bunnings 250 cement Smith
  Residence"), not form-filling.
- Accountant handoff happens via Excel.

The decisions below record what was chosen during Phases 1–4 and the
Capture Hardening Patch, as of HEAD `33957fe`.

## Decision

**Backend.** Python 3.12, FastAPI, SQLAlchemy 2 (async), Alembic,
Pydantic v2, PostgreSQL 16. JWT auth (access + refresh) with bcrypt
password hashing. Three roles: admin, contributor, system. Business
logic lives in `backend/app/services/`; the API layer in
`backend/app/api/` is a thin HTTP/exception translator.

**Web admin (`admin/`).** TypeScript, Vite, React 18, TanStack Query,
Zustand, Tailwind, i18next. Operator/accountant surface only —
review-queue triage, dashboards, Excel export, user management.

**Mobile (`mobile/`).** TypeScript, Expo SDK 54, React Native 0.81,
expo-router, TanStack Query, Zustand, i18next, expo-secure-store.
Intended primary surface for the builder; current state is scaffolded
(login + jobs + settings work; capture is a stub).

**Parser pipeline.** Deterministic rules-pass over a token stream.
Stages, in order: tokenize → amount → job → supplier → category →
payment → duplicate. Each stage emits a narrow result dataclass with a
confidence score. An `LLMParser` seam exists but is wired to
`MockLLMParser` today; the real LLM is not yet integrated.

**Review queue.** A separate `expense_review_queue` table. One open row
per expense (unique constraint). FK on `expense_id` is NOT NULL —
queue rows cannot exist without a parent expense. `review_reasons` is
a non-empty Postgres array of `ReviewReasonCode`.

**Audit.** `expense_audit_log` is append-only. Admin edits to
`reviewed` rows write an audit row; admin edits to `pending` rows do
not (the queue itself records the workflow). Soft-delete on expense
sets `review_status='rejected'` and writes an audit row; rows are
never physically deleted.

**Excel export.** openpyxl, multi-sheet (one per job + an `All
Expenses` master sheet). Formula-injection-safe: any cell value
starting with `=`, `+`, `-`, `@`, tab, or carriage return is escaped.
RFC 5987 dual-form `Content-Disposition` for CJK filename safety.

**Test isolation.** A dedicated `sitetracker_test` database. The
`db_session` fixture in `backend/tests/conftest.py` wraps each test in
a transaction that rolls back at teardown. No test ever hits the live
operative DB.

**Capture Hardening Patch (CHP, May 2026).** Ambiguous job matches
return an actionable HTTP 422 detail instead of silently choosing.
Multi-word English job names match via contiguous-token
concatenation. Amount cap and future-date guards apply on the raw-text
parse path. Duplicate detection is asserted through a full API test.

## Consequences

- The backend is the single source of truth for business logic and
  validation. Frontend code that re-implements validation, GST math,
  or budget rules is an architecture violation.
- `expenses.job_id` is NOT NULL. Ambiguous-job captures cannot be
  persisted; they are rejected at the API edge with an actionable 422.
  Making this nullable is a future ADR, not a casual change.
- `mobile/src/api/types.ts` is hand-maintained today (no automated
  regeneration from OpenAPI). It is currently two lines behind the
  admin equivalent. Drift risk is real and tracked.
- The product name `SiteTracker` remains in user-facing strings.
  Renaming to `Budget AI` is a deliberate future pass; it is not done
  opportunistically.
- The trial baseline is HEAD `b7bf3f1`. The commit on top of it
  (`33957fe`) is `CLAUDE.md` only — no code change, so the trial can
  still run against `b7bf3f1` behaviour.
- New work follows the patterns in `docs/patterns/`. New decisions of
  the scope listed in `docs/adr/README.md` get their own ADR
  (`0002-…`, `0003-…`) which may supersede individual lines of this
  one without rewriting it.
