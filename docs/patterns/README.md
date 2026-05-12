# Implementation patterns

Patterns are reusable templates for the layers AI sessions actually
write against. They exist so each new feature follows the same shape as
the one before it, instead of being rewritten from scratch by guess
every session.

A pattern is not a rule (`CLAUDE.md` carries rules). A pattern is a
**default**. Departures are allowed when justified, but the
justification goes in the PR description and — for sustained
departures — eventually becomes an ADR.

## Format

Every pattern file has exactly six sections, in this order:

```markdown
## Purpose
## When To Use
## Standard Structure
## Rules
## Anti-Patterns
## Testing Expectations
```

No other top-level headers. If a section would be empty, the pattern
is wrong for what it's trying to describe — restructure it, do not
pad it.

## Index

| File | Covers |
|---|---|
| [api-endpoint-pattern.md](./api-endpoint-pattern.md) | new HTTP routes on the backend |
| [service-layer-pattern.md](./service-layer-pattern.md) | backend functions that read or write the DB or apply a business rule |
| [mobile-screen-pattern.md](./mobile-screen-pattern.md) | new screen files under `mobile/app/` |
| [ai-output-pattern.md](./ai-output-pattern.md) | code paths that consume parser or LLM output |
| [review-workflow-pattern.md](./review-workflow-pattern.md) | features that may produce uncertain output and need admin confirmation |

## Adding a new pattern

A new pattern is itself an architectural decision. Before adding one:

1. Confirm an existing pattern does not cover the case. If you are
   tempted to add a 7th section to an existing pattern, the case
   probably belongs in a new ADR, not a new pattern.
2. Open an ADR proposing the pattern (status `Proposed`).
3. Once the ADR is `Accepted`, write the pattern file using the
   six-section format above.
4. Reference the pattern from `CLAUDE.md` if it covers a category
   Claude must consult every session.

Patterns describe **what is**, not what might be. They reference the
files that exemplify the pattern by path. When those files change
shape (e.g. a new exception class on the canonical example), the
pattern is updated in the same PR — patterns are part of the change
surface, not fire-and-forget docs.
