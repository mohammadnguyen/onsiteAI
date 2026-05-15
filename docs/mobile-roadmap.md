# Mobile Roadmap

## Purpose

This document is a living scope inventory for the SiteTracker mobile
app. It exists because the product is mobile-first per CLAUDE.md but
committed feature scope has so far accumulated batch-by-batch with no
single forward-looking reference. The roadmap closes that gap by
stating what mobile v1 covers today, what v1.1 candidates are parked
behind it, what v2-and-later items have been mentioned but not
committed, what production-readiness work the not-demo product needs,
and what is explicitly not planned for v1.

This is product scope guidance, not a feature backlog and not a
delivery plan. It does not replace CLAUDE.md (engineering contract),
ADRs (recorded decisions), `docs/patterns/` (implementation
templates), or per-batch phase plans. It does not bind dates,
ordering beyond v1, or implementation choices.

## Status Legend

- **Shipped** — code on `main`; verified by typecheck + build + (where
  applicable) backend pytest. Phone validation may still be deferred.
- **In progress** — actively under implementation.
- **Approved-not-started** — explicit user approval given for the next
  batch but not yet started.
- **Deferred** — discussed, parked for now. May be picked up later.
- **Not planned for v1** — explicitly excluded from the v1 scope. May
  reappear in v1.1 / v2 with separate validation.
- **Unresolved candidate** — surfaced in conversation; only proceeds
  if real usage proves the need.

## Current Shipped Mobile State (HEAD `b7119f0`)

| Feature | Status | Notes |
|---|---|---|
| Login (JWT + secure-store + EN/ZH labels) | Shipped | Email + password only; no SSO / OAuth. |
| Capture v0 (natural-language input + payment selector + receipt-later toggle + result card) | Shipped | The primary mobile workflow. |
| Recent Captures list (last 20, mine=1, status pills, duplicate flag, receipt-later flag, pull-to-refresh) | Shipped | Accepted as code checkpoint; phone smoke deferred. |
| Jobs read (list + detail modal with aliases + budgets) | Shipped | |
| Admin-only Job Creation Lite (modal: name / code / address / optional aliases) | Shipped | Accepted as code checkpoint; phone smoke deferred. |
| Settings (EN/ZH language toggle + logout + signed-in-as) | Shipped | |
| Mobile Smoke Patch 1 (job-detail close-button reach + tab placeholder-icon suppression) | Shipped | |
| CJK amount parsing in the backend parser (五百五 / 五百块 / 三万二 etc.) | Shipped | Backend-only; no mobile UI surface change. |

## Mobile v1 Scope

**Definition.** Mobile v1 is the current minimum field workflow at
HEAD `b7119f0`, accepted as code checkpoint, with some phone
validation still deferred.

The v1 surface is the rows in the table above. v1 deliberately does
NOT include receipt photo, offline queue, expense detail drill-down,
mobile review queue, mobile expense edit/delete, dashboard content,
or labour content. The trial premise is that the admin web backstops
anything mobile cannot do today (see the responsibility boundary
below).

v1 will be considered "validated" only after a phone smoke pass
against the shipped surface produces no blocker findings. Until that
pass happens, v1 is structurally complete and code-checkpoint-
accepted but field-unverified.

## Admin-Web vs Mobile Responsibility Boundary

The product is mobile-first per CLAUDE.md, but the mobile app does
NOT cover every workflow. The admin web remains primary for several
operational concerns. This boundary is part of the v1 design, not a
gap to be closed.

**Mobile v1 owns:**

- Expense capture from raw natural-language input.
- Recent Captures visibility (read-only confirmation list).
- Job lookup (read-only list + detail view).
- Admin-only Job Creation Lite (name / code / address / optional
  aliases).

**Admin web remains primary for:**

- Review queue triage (resolve / reject pending captures).
- Accountant Excel export.
- Budget setup, category budgets, target margin, warning thresholds.
- Major expense corrections (edit, soft-delete, audit-trail review).
- Team / user administration.
- Supplier and category management.
- Job lifecycle changes beyond Lite create (status, full edit,
  budgets, alias management).

The mobile app may grow into some of these in v1.1 / v2 (see below),
but the v1 trial premise is that the admin uses the web for anything
mobile does not own.

## Mobile v1.1 Candidate Scope

The following items have been mentioned in planning sessions and are
parked for after v1 is validated. Each is a separate batch with its
own approval gate. Order may shift based on real-use findings.

- **Receipt photo capture** — currently **deferred** by explicit user
  decision. Would require a backend model + endpoint + storage choice
  (likely needs an ADR), iOS camera + photo-library permissions,
  mobile preview UI, and admin-side thumbnail rendering.
- **Offline queue / weak-network strategy** — explicitly **not v1**;
  **requires an ADR (sync strategy) before any implementation** per
  the CLAUDE.md ADR Rule. The ADR must define: idempotency-key shape
  on the backend create endpoint; retry UX (silent vs explicit, with
  failure surfacing); duplicate-prevention on retry (interaction with
  the CHP-3 raw-text duplicate window); app-close-while-queued
  behaviour (persistence across termination, recovery on next
  launch). Largest single architectural item on the candidate list.
- **Mobile expense detail (read-only drill-down)** — tap a row in
  Recent Captures to open a detail screen showing: parsed fields
  (amount, supplier, job, category, payment, date); review status
  (reviewed / pending / rejected); review reasons (if pending); and a
  duplicate-suspect warning (if the row was duplicate-flagged). No
  edit and no delete in the first version unless explicitly approved
  in a follow-up batch.
- **Mobile dashboard content** — currently a stub. Status:
  **unresolved candidate**. Only proceeds if real usage during v1
  validation shows the user genuinely needs an on-phone spend
  summary. Otherwise stays a stub.

## Mobile v2 / Later Deferred Scope

Items that have been mentioned but are larger and further out. Listed
without ordering. Each requires its own planning session before
implementation.

- **Mobile expense edit / soft-delete** (admin-web only today).
- **Mobile review-queue surface** — **not a v1 commitment**. Admins
  triage on the web today; on-phone triage is a candidate, not a
  promise.
- **Mobile correction / review visibility** — let the contributor see
  why a capture is pending without leaving the app, and possibly
  correct a narrow set of safe fields if explicitly designed for it.
  The admin web remains the first review surface for now; this item
  is about giving mobile users insight, not full triage power.
- **Mobile labour content** — currently a stub. **Unresolved
  candidate**. Only proceeds if real usage proves it is needed.
- **Mobile category / supplier management.**
- **Mobile Excel export trigger or share-sheet handoff.**
- **Push notifications** (rejection, duplicate suspect, weekly
  summary) — **not a v1 commitment**.
- **Real LLM swap-in** for the parser's `MockLLMParser` seam —
  **requires an ADR plus a safety plan** (prompt-hash logging,
  latency budget, kill switch, PII handling) before any
  implementation.
- **User / team management** — invite user, deactivate user, assign
  role, reset / change password, owner / admin / contributor role
  boundaries, tenant or team setup. Currently single-user-wears-
  both-hats. Multi-user is the dependency for any real team
  workflow.
- **Job lifecycle management** beyond Lite create — close / archive
  jobs; hide closed jobs from capture suggestions and parser
  matching; rename job; add or remove aliases; surface
  duplicate-or-similar-job warnings at create time; show per-job
  budget visibility on the phone.
- **Auth flows beyond email + password** — SSO / OAuth, password
  reset, profile edit.
- **Accounting integrations** (Xero / MYOB / QuickBooks) — **not
  planned for v1**; would require separate scope discussion if
  proposed.

## Production Readiness / Not-Demo Requirements

This product is intended for real operational use, not a demo. The
following items are roadmap scope but NOT immediate implementation —
each will need its own planning session and (where applicable) ADR
before work starts. They are listed here so the production cut is
not surprised by their absence later, and so they are not confused
with mobile feature work.

- **Hosted backend with HTTPS** — production deployment target
  separate from local dev. TLS termination, certificate management,
  no plaintext API surface.
- **Production database** — managed Postgres (or equivalent)
  separate from the local Docker `sitetracker` DB used for dev. Not
  shared with dev or staging.
- **Backup and restore procedure** — scheduled backups, tested
  restore drill, documented retention policy.
- **Dev / staging / production environment separation** — distinct
  databases, distinct API URLs, distinct credentials, no
  cross-environment writes possible.
- **Production mobile API URL strategy** — how the Expo build
  resolves the API base URL across dev / staging / production
  (build-time env var, runtime config, or per-build flavour).
- **App distribution path** — TestFlight for iOS, Play internal
  testing for Android (later). Code signing, beta release flow,
  store listing, review submission.
- **Error logging** — structured backend logs, mobile crash
  reporting, alert thresholds, log retention policy.
- **Basic monitoring / uptime checks** — health endpoint pinged
  externally; alert on backend down, DB unreachable, auth failure
  spike.
- **Migration process** — how Alembic migrations get applied to the
  production database safely (forward-only by default, rollback drill
  documented, no destructive migrations without approval).
- **Secrets management** — JWT secret, DB password, future LLM API
  keys, future tunnel / push-service tokens. Not committed to git;
  rotation procedure documented; access scoped per environment.

These items deliberately have no v1 / v1.1 / v2 label because they
cut across the whole product and are gated by the trial timeline
rather than the mobile feature schedule.

## Explicitly Not Planned for v1

Items that have surfaced in conversation as plausibly-adjacent but
are explicitly excluded from v1 scope. None of these are permanent
rejections — they may reappear in a later release with separate
validation.

- Voice input — **not planned for v1**; would route through an
  LLM/service and would need its own scope discussion.
- OCR / receipt-text extraction — **not planned for v1**; receipt
  photo (v1.1 candidate) is intended for human auditability, not
  machine extraction.
- Construction calculators (concrete volume, board-feet, etc.) —
  **not planned for v1**.
- Time tracking / clock-in / clock-out — **not planned for v1**.
- Subcontractor invoicing — **not planned for v1**.
- In-app messaging / chat — **not planned for v1**.
- WHS / compliance modules — **not planned for v1**.
- Photo gallery beyond receipts — **not planned for v1**.
- Marketplace / job board — **not planned for v1**.

Adding to this list requires a one-sentence reason and approval.
Removing from this list (i.e. promoting an item to v1.1 / v2)
requires the same approval rigour as a new feature decision.

## Relationship to CLAUDE.md, ADRs, and `docs/patterns/`

- **CLAUDE.md** is the always-on engineering contract. It sets the
  iOS-First Rule, the AI-assistive rule, the architecture rules, and
  the ADR-trigger list. The roadmap inherits these and does not
  restate them.
- **ADRs** record decisions already taken. The roadmap is forward-
  looking and never doubles as an ADR. When a v1.1 / v2 / production-
  readiness item is approved for implementation AND lands on the
  CLAUDE.md ADR-trigger list (auth / sync / mobile architecture / AI
  orchestration / review workflow / queues / extraction /
  deployment), an ADR is written at that point — not preemptively
  from this roadmap.
- **`docs/patterns/`** holds implementation templates. Every shipped
  mobile screen still follows `mobile-screen-pattern.md` and the
  response-packet rule. The roadmap does not restate patterns.
- **Phase plans** in `docs/` are one-shot per-batch plans. The
  roadmap is the umbrella that points at them historically; it does
  not duplicate their detail.

## Update Rules

- A row moves from **Approved-not-started** → **In progress** →
  **Shipped** in the same PR that ships the code. The status table
  update lives in that PR.
- New v1.1 / v2 / production-readiness candidate items get added
  only after a planning session has surfaced them.
- Removing an "Explicitly Not Planned for v1" entry requires the
  same approval rigour as adding a new v1 feature.
- This document is revised, not enforced. When real usage
  contradicts the roadmap, real usage wins and the roadmap is
  updated.
- Every change to this document is reviewed in the PR that includes
  it.

## Anti-Patterns

- Treating this as a Gantt chart, OKR doc, or quarterly delivery
  plan. It is none of those.
- Listing dates or estimates per item. Dates are not roadmap content.
- Adding a feature to v1 without explicit approval.
- Letting "Approved-not-started" rows accumulate. If approval was
  given but the batch never started, the row should either be in
  progress or be moved back to a candidate state.
- Using this doc as a substitute for CLAUDE.md, ADRs, patterns, or
  phase plans.
- Letting the v2 list grow indefinitely. Items not actively
  considered after a few cycles should be pruned.
- Conflating production-readiness scope with mobile feature scope.
  They are tracked separately on purpose.
- Using language like "permanent" or "never" in any status column.
  Use "deferred" / "not planned for v1" / "requires separate
  validation" instead.
