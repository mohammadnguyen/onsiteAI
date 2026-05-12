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

For significant changes:
- wait for approval before implementation

Significant changes include:
- DB schema changes
- auth changes
- queue architecture changes
- AI pipeline changes
- major dependency additions
- large refactors

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
