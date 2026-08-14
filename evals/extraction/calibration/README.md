# Calibration data layout and separation rules

**Governing schema:** `../annotation-schema-v0.1.md` (types, support levels, privacy).
**Independence rules:** founder labels real captures first and independently; the
one-week blind relabel is real elapsed time — neither is ever produced,
suggested, or simulated by Claude/AI tooling.

## Real data never enters this repository (founder ruling, 2026-08-14)

This repository is public. Therefore **no real capture, gold label,
held-out case, or blind-shuffle order mapping is ever committed here** —
not even privacy-scrubbed. All of it lives in a private workspace
outside the repo (default `D:/FOREY_PRIVATE_CALIBRATION/`, never copied
into a worktree).

The tooling reads that location from `--private-root` or the
`FOREY_PRIVATE_CALIBRATION` environment variable. There is no
`dataset.v0.jsonl` in this repository and there must never be one.

## Data classes — never combined into one count

| Class | Location | Counts toward Baseline v0.1? |
|---|---|---|
| Shipped worked examples | `../dataset.sample.jsonl` (3 lines, `SAMPLE-*`) — in repo | **No** — schema-design material, not independent evidence |
| Reference examples | private `reference.jsonl` (`REF-*`) | **No** — may have influenced design/prompts |
| Synthetic policy-calibration | private `synthetic.jsonl` (`SYN-*`) | **No** — tests schema/policy behaviour only |
| Raw + labelled real captures | private `dataset.v0.jsonl` (`R-*`) | **Yes**, once founder-labelled and frozen |
| Held-out real captures | private `dataset.heldout.jsonl` (`meta.held_out: true`) | Reserved — never used to adjust schema or prompts |
| Blind-relabel order mapping | private, outside repo (tool-enforced) | n/a |

Rules:

1. `gold: null` until the founder's independent first pass. Tooling may
   validate structure; it may not propose semantic labels.
2. Baseline v0.1 requires **≥ 30 frozen, founder-labelled real cases**;
   `validate_dataset.py --baseline-ready --private-root <path>` is the
   deterministic check. No synthetic/reference item ever substitutes
   toward that threshold.
3. Blind relabel: `tools/blind_shuffle.py` produces a gold-stripped,
   shuffled worksheet; the order mapping is refused if it would land
   inside the repo. `tools/label_diff.py` compares the two founder
   passes afterwards; disagreements become worked examples (schema §10),
   also kept private until the founder chooses to generalise one.
4. Founder-maintained, append-only in the private workspace:
   `ambiguity-log.md`.

## Current repository state (2026-08-14)

- Shipped worked examples in repo: 3 (`dataset.sample.jsonl`).
- Reference / synthetic / real / held-out: **not in this repository, by
  policy.** Their counts are reported from the private workspace at
  checkpoint time and are 0 in-repo by design.
- Baseline v0.1: not started. Frozen real cases: 0/30.
