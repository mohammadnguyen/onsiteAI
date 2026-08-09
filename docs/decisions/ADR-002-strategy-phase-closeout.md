# ADR-002 — Strategy Phase Close-out

**Status:** Accepted
**Date:** August 2026

## State at close

- Product strategy phase: **CLOSED**
- Charter: **v1.0 SIGNED** (`docs/product/forey-charter-v1.0.md`) — Amendments 1–5 at signing; Amendments 6–9 added post-close, pre-landing (2026-08-09)
- Implementation authority: **`docs/product/PRODUCT.md`** (Slice-1 binding subset; every section registry-backed — enumerate via `scripts/check_decision_drift.py --print-hashes`)
- Automation governance: **DECIDED** (ADR-001)
- Slice-1 ontology: **LOCKED** — Site Log Fact / Task / Potential Variation (DEC-ONTOLOGY-001)
- Decision drift mechanism: **ACTIVE** — stable Decision IDs + normalized-hash acknowledgement + DECIDED body-coverage rule (Amendment 7), enforced by `scripts/check_decision_drift.py --require-full-coverage` (scope-aware per Amendment 9) as an L1 CI gate, itself fixture-tested (`tests/test_check_decision_drift.py`)

## Execution order (binding sequence)

1. Repo audit — map existing modules to Slice-1 foundation vs untouched, and determine whether an existing storage/evidence implementation or open PR already satisfies DEC-EVIDENCE-001 (last known open item at close: PR 6, Tigris storage — verify against the repo, do not assume; the storage provider is an implementation choice, not a decision). Any unresolved prerequisite is surfaced and closed first as its own gated PR.
2. Land governance docs (Charter, ADRs, PRODUCT.md, drift check in CI).
3. `evals/extraction/annotation-schema-v0.1.md` finalised.
4. (Parallel, founder) raw corpus collection — daily real site captures, mundane samples included, pure-English included.
5. Founder annotation calibration set (10–20 items — tests the schema, produces no baseline metrics), blind re-label after one week, disagreements become worked examples.
6. Baseline v0.1 — 30+ labelled samples, naive prompt, numbers recorded however ugly.
7. Slice-1 implementation plan (plan-review skill applies).
8. Code.

## Backlog discipline

Any new idea arriving mid-sequence (quoting, Gantt, new candidate types, integrations…) is recorded in the backlog **only**. It does not enter planning without evidence and does not interrupt the sequence.

## Re-entry bar for strategy discussion

Strategy-level discussion reopens only with real evidence in hand: baseline eval numbers plus per-failure reading notes (e.g. "PV recall 72%, precision 91%, wrong-job 0%; 3 misses were all indirect client requests"). Prediction: the first baseline numbers should look bad — their function is to create an immediate measuring stick, not to look good.
