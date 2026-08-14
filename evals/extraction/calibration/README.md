# Calibration data layout and separation rules

**Governing schema:** `../annotation-schema-v0.1.md` (types, support levels, privacy).
**Independence rules:** founder labels real captures first and independently; the
one-week blind relabel is real elapsed time — neither is ever produced,
suggested, or simulated by Claude/AI tooling.

## Data classes — never combined into one count

| Class | File | Counts toward Baseline v0.1? |
|---|---|---|
| Shipped worked examples | `../dataset.sample.jsonl` (3 lines, `SAMPLE-*`) | **No** — schema-design material, not independent evidence |
| Reference examples | `calibration/reference.jsonl` (`REF-*`) | **No** — may have influenced design/prompts |
| Synthetic policy-calibration | `calibration/synthetic.jsonl` (`SYN-*`) | **No** — tests schema/policy behaviour only |
| Real captures | `../dataset.v0.jsonl` (`R-*`) | **Yes**, once gold is founder-labelled and frozen |
| Held-out real captures | `../dataset.heldout.jsonl` (`R-*`, `meta.held_out: true`) | Reserved — never used to adjust schema or prompts |

Rules:

1. Real captures enter the repo only privacy-scrubbed per schema §6; the
   pseudonym mapping lives outside the repository, always.
2. `gold: null` until the founder's independent first pass. Tooling may
   validate structure; it may not propose semantic labels.
3. Baseline v0.1 requires **≥ 30 frozen, founder-labelled real cases**
   (`python evals/extraction/tools/validate_dataset.py --baseline-ready`
   is the deterministic check). No synthetic/reference item ever
   substitutes toward that threshold.
4. Blind relabel: `tools/blind_shuffle.py` produces a gold-stripped,
   shuffled worksheet; the order mapping is written OUTSIDE the repo.
   `tools/label_diff.py` compares the two founder passes afterwards;
   disagreements become worked examples (schema §10) and live in
   `calibration/worked-examples/`.
5. Founder-maintained, append-only: `calibration/ambiguity-log.md`.

## Current repository state (2026-08-14)

- Shipped worked examples: 3 (`dataset.sample.jsonl`).
- Reference / synthetic / real / held-out files: **not yet in the
  repository.** Founder-reported materials (10 reference, 30 synthetic,
  15 real) exist privately if at all — they are not repo facts until
  landed here in scrubbed form.
