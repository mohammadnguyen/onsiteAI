# Budget AI — CLAUDE.md

## Product Context

Budget AI is an iOS-first operational product for real-world construction workflows.

Primary use cases:
- fast mobile capture
- job/budget tracking
- AI-assisted extraction/classification
- review workflows
- weak-network field usage
- photo/document uploads
- operational visibility

This is NOT a demo project.

The goal is:
- long-term maintainability
- controlled complexity growth
- stable architecture
- AI-assisted engineering with human governance

---

# Core Engineering Philosophy

Prefer:
- simple systems
- explicit logic
- deterministic behavior
- stable patterns
- small reversible changes

Avoid:
- unnecessary abstraction
- premature optimization
- architecture churn
- hidden side effects
- speculative engineering

Working software with clear structure is preferred over “clever” systems.

---

# Most Important Rule

Do NOT assume.

If requirements, architecture impact, or intent are unclear:
- stop
- explain uncertainty
- ask focused questions

Avoid fake certainty.

---

# Current Project State

This repository is under active iterative development.

Priorities:
1. preserve momentum
2. avoid architecture collapse
3. avoid hidden technical debt
4. keep iteration fast but controlled

Do NOT introduce enterprise-level process overhead.

---

# Product Authority

- `docs/product/PRODUCT.md` is the **current implementation authority**. Every feature/schema decision must be consistent with it.
- `docs/product/forey-charter-v1.0.md` is strategic context. Items labelled **DIRECTION** or **NOT NOW** are **not buildable** — do not implement them "small" as a side effect of another task.
- If a requested task conflicts with any `DEC-*` decision referenced in PRODUCT.md: **STOP**, quote the conflicting decision, and wait for the founder's adjudication.
- Exception — existing surfaces (DEC-EXISTING-001): maintenance of pre-Charter shipped functionality (expenses, labour, budget, cost views) is **not** a conflict. Bug fixes, regression fixes, security/compatibility fixes, test repair, correctness fixes, small UX repairs, necessary maintenance refactors, and minimal schema changes required to restore existing behaviour proceed under the light gate. Not maintenance: new workflows, scope expansion, large features, whole-module redesign, or removing existing capability because the Charter doesn't mention it. New capability requires promotion through PRODUCT.md first. Codebase conventions override spec literals, but deviations are surfaced and adjudicated, never silently applied.

---

# Hard Boundaries (schema-level, not prompt-level)

- No code path may allow AI-generated candidates to write Truth-designated records (confirmed events, variation status, commercial fields) without a human confirmation step. This is enforced in schema/service design and checked in review (DEC-AI-BOUNDARY-001).
- Candidate, confirmation-delta (evidence ref / candidate-as-proposed / human edit / final), `occurred_at` vs `created_at`, and audit history are schema requirements from the first migration that touches them (DEC-EVIDENCE-001, DEC-TIME-001).

---

# Gate Sequence

Backend PRs: `ruff check` → `pytest` → `python -m pytest tests/test_check_decision_drift.py -q` → `python scripts/check_decision_drift.py --require-full-coverage` — all verbatim output, all green (governance checks may not be skipped, and the drift check runs from the repository root, not `backend/`; the active scope is read from PRODUCT.md's `Binding Scope:` line, `--scope` exists only as an override).
Mobile PRs: `tsc --noEmit` verbatim.

Two gate tiers ("significant" is defined by the Full Gate list):
- **Full gate** (plan-review skill + all checks + explicit approval before mutation): schema migrations, auth/security changes, destructive or irreversible actions (including external side effects), architecture changes, public API behaviour changes, changes to PRODUCT.md binding content, significant new capabilities, major new dependencies, anything touching the extraction/confirmation pipeline.
- **Light gate** (checks + STOP): existing-maintain work, bounded bug fixes, tests, small UX repairs, low-risk internal corrections.

---

# Eval Rule

Once the extraction eval harness exists: any change to extraction prompts, context injection, or candidate schema requires re-running the baseline eval and reporting the numbers in the PR alongside the previous baseline.

---

# iOS-First Rule

Design for real mobile usage first.

Assume:
- users are on-site
- users are distracted
- network may be unstable
- sessions are short
- users need speed over complexity
- one-handed use matters

Do NOT design workflows that only work well on desktop.

The mobile workflow is the product.
Web/admin is secondary.

---

# AI System Rules

AI is assistive, not authoritative.

AI may:
- classify
- summarize
- extract
- suggest
- rank
- assist workflows

AI must NOT:
- silently mutate business-critical data
- bypass review flows
- fabricate structured data
- invent quantities or financial values
- make irreversible decisions automatically

Low-confidence outputs must surface uncertainty.

Deterministic systems remain source of truth.

---

# Architecture Rules

Frontend responsibilities:
- UI
- interaction
- local state
- user workflows

Backend responsibilities:
- business logic
- validation
- orchestration
- persistence
- security

Do NOT:
- place business logic in frontend
- allow frontend to become source of truth
- tightly couple unrelated modules
- bypass service layers

---

# Stable Patterns Rule

Reuse existing patterns before inventing new ones.

Consistency is more important than novelty.

Before introducing a new pattern:
- explain why existing patterns fail
- explain tradeoffs
- explain long-term impact

Avoid pattern explosion.

---

# File Discipline

Prefer:
- small focused files
- clear naming
- explicit boundaries

Soft limits:
- frontend components: ~250 lines
- backend services: ~400 lines

Large files require justification.

---

# Database Rules

Database changes are high-risk.

Do NOT:
- modify schemas silently
- introduce destructive migrations
- change critical data flows without approval

Prefer:
- additive migrations
- reversible changes
- auditability
- traceable operations

---

# Testing Rules

Every feature must include:
- happy path validation
- failure handling
- edge-case consideration

Critical workflows require:
- regression protection
- acceptance validation

No feature is complete if:
- tests fail
- build fails
- behavior is unverified

---

# Logging & Observability

Critical workflows should log:
- pipeline stages
- failures
- retries
- AI confidence
- state transitions

Logs should help debug real production failures.

Avoid noisy meaningless logging.

---

# Technical Debt Rule

Shortcuts are allowed ONLY if:
- explicitly acknowledged
- isolated
- reversible
- documented

Do NOT leave silent hacks.

---

# Workflow Rules

Before coding:
1. explain current behavior
2. explain proposed approach
3. identify affected files
4. identify risks
5. identify required tests

For significant changes, the two gate tiers under **Gate Sequence** apply: full-gate changes require plan-review and explicit approval before implementation; light-gate changes run checks then STOP. "Significant" is defined by the Full Gate list.

---

# Implementation Style

Prefer:
- incremental changes
- low-risk modifications
- preserving working systems
- minimal scope changes

Do NOT:
- rewrite stable systems unnecessarily
- refactor unrelated files during feature work
- mix large architecture changes into bugfixes
- introduce complexity without measurable value

---

# Communication Style

Be concise and direct.

When responding:
- explain reasoning
- identify assumptions
- identify risks
- separate facts from speculation
- avoid overconfidence

Do not pretend something was verified if it was not.

---

# Response Packet Rule

Every response must end with exactly one fenced markdown block labelled REVIEW_PACKET.

The full 15-section template lives in docs/patterns/response-packet-pattern.md.

A response that runs the plan-review or sceptic-review skill emits the skill's fixed output block first, then still ends with the standard REVIEW_PACKET.

This is an output-format rule only.

It does not override:
- CLAUDE.md
- docs/adr/
- docs/patterns/
- approved phase plans
- accepted product decisions
- safety and data-integrity rules

---

# ADR Rule

Major architectural decisions must be recorded as ADRs.

Required for:
- auth changes
- sync strategy
- mobile architecture
- AI orchestration
- review workflows
- queue systems
- extraction architecture
- deployment architecture

Keep ADRs concise.

---

# Product Reality Rule

This product is intended for real operational usage.

Prioritize:
- reliability
- clarity
- usability
- maintainability
- operational speed

NOT:
- impressive architecture
- theoretical purity
- unnecessary complexity
- feature quantity over workflow quality

The goal is not:
“AI-generated code”

The goal is:
“Reliable AI-assisted software systems.”
