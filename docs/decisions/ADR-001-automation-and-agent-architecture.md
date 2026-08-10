# ADR-001 — Automation & Agent Architecture

**Status:** Accepted
**Date:** August 2026
**Scope:** How Forey is developed (the development system), and the governing rules for any automation added to that system. Product-side automation boundaries live in the Charter (DEC-AI-BOUNDARY-001, DEC-AUTONOMY-001); this ADR is consistent with them.

## Context

Forey is developed by a solo founder with Claude Code as the primary development engine. The binding constraint is not code generation speed — it is **founder verification bandwidth**. Any architecture that increases output volume without increasing verifiability makes the project worse.

## Decision

### 1. Single primary agent + artifacts, not an agent team

Development uses one primary Claude Code agent. Durable value lives in **artifacts** — specs, schemas, ADRs, decision logs, eval datasets, fixtures — not in agent personas. Agent personas sharing the same underlying model do not produce capability differences; they produce the illusion of independent review.

### 2. Four-layer automation ladder

Every automated capability in the development system sits on exactly one layer:

- **L1 — Deterministic tool**: scripts, linters, CI checks, schema validators. No model calls. Examples: `ruff`, `pytest`, `tsc --noEmit`, `check_decision_drift.py`, AST thinness assertions.
- **L2 — Skill**: a versioned procedure executed by the primary agent in-context (`.claude/skills/`). Uses model judgment, but the procedure, inputs and output format are fixed and reviewable.
- **L3 — Subagent**: a clean-context invocation for genuine isolation needs (e.g. sceptic review that must not see the author's reasoning).
- **L4 — Service/agent**: independently deployed, with its own lifecycle, state, credentials.

### 3. Lowest-intelligence-layer principle

New capability defaults to the **lowest layer that can do the job**. Moving right (up the ladder) requires written justification against the upgrade triggers below. Anything currently done by an LLM that can be mechanised **must move down** — with one condition: the mechanical version must first prove non-inferior on the relevant eval, and remains under eval after the move.

### 4. Hard boundaries live below the LLM

Prompt boundary < application boundary < credential/schema boundary. Anything that must never happen (extraction writing Truth tables, agents touching production credentials) is prevented by schema, permissions and credentials — never by prompt text alone. This mirrors the product rule DEC-AI-BOUNDARY-001.

### 5. Skills are executable governance, not personas

Only two true skills exist at present: **plan-review** and **sceptic-review**. Extraction evaluation is a script (L1) with human scoring, not a skill. Skills are stochastic programs: each skill needs fixtures with known-flaw inputs and expected-finding outputs, and is tested like code. A skill that has never caught a planted flaw is decoration.

### 6. Sceptic review

Automation upgrade proposals (L→L+1) are evaluated by the sceptic-review skill running in a clean-context subagent, answering six questions: (1) What evidence shows the current layer failing? (2) What data does the new layer depend on? (3) What behaviour does it depend on? (4) What is the failure consequence? (5) What is the simpler alternative? (6) What is the kill criterion? **Only the founder signs kill criteria.**

### 7. Gates and escaped defects

Gates report **pass / fail / qualified** — uncertainty is surfaced, never folded into pass (precedent: R-7, where `pg_restore` exit 1 on a harmless warning was recorded as QUALIFIED PASS rather than silently accepted). Every escaped defect is dispositioned into exactly one of: new eval case, new fixture, or new deterministic check. No fourth option ("noted").

### 8. Success metric

The development system is measured by **founder attention per verified change**, guarded by escaped-defect tracking — not by lines generated or PRs per week.

## Rejected alternative

**Multi-agent role team (PM / Architect / Builder / QA personas).** Rejected because: personas on one model are prompt variations, not independent reviewers; coordination overhead lands on the founder; it multiplies output volume against a fixed review budget; and the claimed benefits (separation of concerns, adversarial review) are achieved more cheaply and more honestly by artifacts + clean-context subagent invocation where isolation genuinely matters.

## Reopen conditions (upgrade triggers)

A capability may move up a layer only when at least one of these is demonstrated, and review capacity exists:

1. **Context isolation** — correctness requires not seeing the primary context.
2. **Permission isolation** — the capability needs credentials the primary agent must not hold.
3. **Resource specialization** — a genuinely different runtime/model/toolset is required.
4. **Genuine parallelism** — wall-clock parallel work whose outputs the founder can actually review.

Service (L4) additionally requires: independent lifecycle, own state, event-driven operation, restricted credentials, distinct failure semantics. The first true L4 service is expected to be **Forey's own extraction pipeline** — a product component, not a team member.

## Lineage

Existing precedents this ADR generalises: AST thinness assertions (L1 guarding architecture), zero-if routers, 404-not-403 cross-job semantics (boundary below the app layer), R-7 qualified pass (gate honesty), soft-delete global filter (schema-level invariants over convention).
