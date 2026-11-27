# ADR-002 exception 001 — Manual Potential Variation ahead of the baseline

**Status:** Accepted (founder, 2026-11-07)
**Excepts:** ADR-002 "Execution order (binding sequence)": step 8 (Code) before step 6 (Baseline v0.1)
**Decision it serves:** DEC-PV-001 (Charter Amendment 11)

## Decision

The manual Potential Variation package may be implemented before Baseline v0.1 exists: a person flags an existing Site Log entry for the builder to check, finds it by Job, and the builder moves it between To Check, Following Up and Follow-up Ended, keeping the reason when follow-up ends.

## Reason

- The package touches no extraction prompt, context injection, candidate schema or eval set. The baseline measures extraction; this package neither uses nor changes it.
- The founder chose to test the human workflow now and accepted that its commercial value is unverified. Real usefulness is still judged by DEC-GATE-H3-001.
- Site Log and Task were also built before any baseline result existed in the repository. This record makes the exception for this package explicit instead of implied.

## Scope

- This package only: the change that introduces DEC-PV-001's manual flag, with its docs, schema, API, mobile screens and tests.
- Not covered: AI-proposed Potential Variations, any extraction or eval work, any other feature. The AI line stays paused.
- ADR-002's sequence is otherwise unchanged. Any other feature that wants to go ahead of the baseline needs its own exception, with its own reason and scope.

## Consequences

- Baseline v0.1 is still required before the AI line resumes, including any work that would let extraction propose Potential Variations.
- The CLAUDE.md eval rule is unaffected: nothing here changes extraction.
