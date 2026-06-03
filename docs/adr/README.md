# Architectural Decision Records

An ADR in this repo is a **short record of a decision that has already been
taken**, written in past tense. It exists so a new contributor — or a
fresh Claude session — can find the *why* without reverse-engineering it
from git history.

ADRs do not propose, debate, or speculate. Use a plan file (and the
plan-mode workflow) for that work. Once a decision is settled and a
change has shipped, write the ADR.

## Status values

| Status | Meaning |
|---|---|
| `Proposed` | Drafted but not yet accepted. Avoid this status — write the ADR after the decision is taken, not before. |
| `Accepted` | The decision is in effect. |
| `Superseded by NNNN` | A later ADR replaced this one. Both stay in the directory. The successor ADR's Context section names this number. |
| `Deprecated` | The decision is no longer in effect, with no successor (the area was removed, or the choice no longer applies). |

No other status values are allowed.

## Numbering

4-digit, sequential, never reused. `0001`, `0002`, `0003`, …

A superseded ADR keeps its number. Its file is not deleted; only the
`Status` line changes.

## Format

Every ADR has exactly four sections, in this order:

```markdown
# NNNN — <title>

## Status
Accepted (YYYY-MM-DD)

## Context
<one short paragraph: what constraint or problem drove this decision>

## Decision
<the actual choice; can be bullets or a small table; present tense>

## Consequences
<what this implies day-to-day; bullets are fine>
```

No `Alternatives Considered`, `Discussion`, `Open Questions`, or other
headers. If a section would be empty or trivial, the ADR is too small
to need writing — record it as a one-line note in the relevant pattern
file instead.

## When to write an ADR

Per `CLAUDE.md` ADR Rule, write one for:

- auth changes
- sync strategy (mobile ↔ backend offline behaviour)
- mobile architecture (the shape of the Expo app, not individual screens)
- AI orchestration (LLM seam, parser pipeline changes)
- review workflow (the queue's state machine, not individual reasons)
- queue systems
- extraction architecture (parser stage layout)
- deployment architecture

Do not write an ADR for: adding a new endpoint, adding a new screen,
adding a new test, fixing a bug, renaming a variable.

## Index

| Number | Title | Status |
|---|---|---|
| [0001](./0001-current-architecture.md) | Current Architecture | Accepted |
| [0002](./0002-environment-and-secrets-strategy.md) | Environment and Secrets Strategy | Accepted |
| [0003](./0003-staging-deployment-strategy.md) | Staging Deployment Strategy | Accepted |
| [0004](./0004-mobile-testflight-distribution.md) | Mobile TestFlight Distribution | Accepted |
