---
name: plan-review
description: Run before implementing any full-gate change (schema, API contract, extraction/confirmation pipeline). Verifies an implementation plan against PRODUCT.md, the Decision Registry, ADR-001, and existing codebase conventions before any code is written.
---

# plan-review

This is a procedure, not a persona. Execute the steps and produce the output format exactly.

## Inputs

- The proposed task/plan (from the founder or the current session).
- `docs/product/PRODUCT.md`
- Charter Decision Registry (`docs/product/forey-charter-v1.0.md`, Part A)
- `docs/decisions/ADR-001-automation-and-agent-architecture.md`
- Relevant existing code, schema and conventions (UUID keys, SoftDeleteMixin, 404 semantics, thinness assertions).

## Procedure

1. Restate the task in one sentence. If it cannot be restated in one sentence, split it first.
2. Scope check: list every PRODUCT.md binding section the task touches. If it touches an item in "Explicitly out of Slice 1" or the NOT NOW list → STOP, report the conflict, do not plan further.
3. Boundary check: does any data path let model output reach a Truth-designated write without confirmation? If yes → redesign before proceeding.
4. Schema check: for any migration — evidence retention, confirmation delta, occurred_at/created_at, audit, soft-delete, UUID keys all respected? Rollback stated?
5. Convention check: list deviations from existing codebase conventions and mark each SURFACE (needs founder adjudication) or CONFORM.
6. Slice check: what is explicitly NOT being built in this task (nearest tempting adjacencies)?
7. Test plan: which gates apply (full/light), which new tests/fixtures, which eval implications.

## Output format

```
PLAN-REVIEW
Task: <one sentence>
Bindings touched: DEC-..., DEC-...
Conflicts: none | <list, each with the decision quoted>
Boundary: pass | redesign-required (<why>)
Schema: pass | issues (<list>)
Deviations to surface: <list or none>
Explicitly excluded: <list>
Gates: full | light — <checks>
Recommendation: proceed | revise | STOP
```

A plan-review with `Conflicts` or `Deviations to surface` non-empty always ends the turn with STOP and waits for the founder.

This block supplements, not replaces, the repository's Response Packet Rule: emit this block first, then end the response with the standard REVIEW_PACKET.
