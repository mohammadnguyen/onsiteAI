# Calibration data layout and separation rules

## Schema routing

- **`../annotation-schema-v0.2.md` is CURRENT** — new corpora are authored
  against it. A corpus is validated as v0.2 iff a sidecar
  `<name>.manifest.json` exists (schema_version "0.2" + the five provenance
  fields). v0.2 adds: corpus provenance manifest, `context.job_state`
  (confirmed | unassigned, cross-validated against `context.job`),
  optional `fact_category` on site_log_fact (DEC-ONTOLOGY-001 Amendment
  10), `meta.modality`, and `meta.routing_case: multi_job` exclusion.
- **`../annotation-schema-v0.1.md` is HISTORICAL** — preserved unchanged;
  corpora without a manifest validate as legacy v0.1 (warning) and
  contribute ZERO cases to v0.2 Baseline readiness.
- Provenance is corpus-level and homogeneous (no record-level overrides);
  declared provenance is authoritative, and the storage path must agree
  with it. `event_origin: real` is forbidden anywhere in this public repo.
- Independent Baseline eligibility (all five required): event_origin real,
  creation_method contemporaneous_capture, verbatim_capture true,
  ai_exposure none, intended_use **heldout**. Development corpora are
  development-ready only — never Baseline evidence.
- Historical private manifests using ai_raw_exposed/ai_gold_exposed map to
  ai_exposure for documentation only (raw_seen = raw true + gold false;
  gold_seen = gold true regardless of raw; none = both false) and are not
  accepted as v0.2 manifests.

**Governing schema:** `../annotation-schema-v0.2.md` (CURRENT — corpus
manifest, job_state, fact_category, modality, eligibility).
`../annotation-schema-v0.1.md` is HISTORICAL and governs legacy corpora
only (types, support levels and privacy are inherited by v0.2 unchanged).
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

**Enforcement — `tools/path_policy.py`.** Every private path a tool
touches (dataset, worksheet, order mapping, diff report, private root)
is checked against **every registered Git worktree**, enumerated with
`git worktree list --porcelain` — not just the worktree the tool runs
from, because the primary checkout and other `git worktree add`
locations live elsewhere on disk. Worksheets and diff reports quote
verbatim utterances and gold, so they are guarded exactly like the
dataset. Refusals **fail closed** (if the worktree list cannot be
obtained, the tool refuses to run), exit **2**, name the offending
argument and worktree, and leave **no partial output** — the check runs
before anything is opened for writing.

`.gitignore` carries narrow patterns for the same files as
**defence-in-depth**: a backstop that reduces the blast radius of a
hand-created or stray file. It does not make an accidental commit
impossible — a renamed or relocated file still slips past it. The tool
guard, not the ignore list, is the protection.

## Data classes — never combined into one count

| Class | Location | Counts toward Baseline v0.1? |
|---|---|---|
| Shipped worked examples (**public fixture**) | `../dataset.sample.jsonl` (3 lines, `SAMPLE-*`) — in repo | **No** — schema-design material, not independent evidence |
| Synthetic policy-calibration (**public fixture**, optional) | `synthetic.jsonl` — in repo *or* private | **No** — tests schema/policy behaviour only |
| Reference examples | private `reference.jsonl` (`REF-*`) | **No** — may have influenced design/prompts; **private by safe default** because it can be real-derived. Promotable to a public synthetic fixture only once positively established as wholly fabricated (schema §6). |
| Raw + labelled real captures (**development**) | private `dataset.v0.jsonl` (`R-*`, manifest `intended_use: development`) | **No — never.** Development data is for schema/prompt calibration (development-ready) and contributes ZERO cases to an independent Baseline |
| Held-out real captures | private `dataset.heldout.jsonl` (manifest `intended_use: heldout`) | **The only possible Baseline input** — and only with the full eligibility quintuple (real, contemporaneous, verbatim, ai_exposure none, heldout) plus founder human-process confirmation. Never used to adjust schema or prompts |
| Blind-relabel order mapping | private, outside repo (tool-enforced) | n/a |

Rules:

1. `gold: null` until the founder's independent first pass. Tooling may
   validate structure; it may not propose semantic labels.
2. **The machine gate is structural only — and held-out only.**
   `validate_dataset.py --baseline-structure-ready --private-root <path>`
   checks one thing: ≥ 30 structurally valid labelled cases in the
   HELD-OUT corpus whose manifest satisfies the full eligibility
   quintuple, excluding `multi_job`-routed records. Development, legacy
   (no manifest), synthetic, reference, retrospective and AI-exposed
   data contribute zero — hard exclusion, not a warning. A present,
   well-formed `gold` object proves a *label exists* — it cannot prove
   the founder labelled independently, that a real week elapsed, that
   the blind relabel happened, that disagreements were resolved, or
   that the dataset is frozen. Those are human facts, and **Baseline
   v0.1 requires the founder's explicit confirmation of them** on top
   of the structural gate. The tool's own output says so.
3. Blind relabel: `tools/blind_shuffle.py` produces a gold-stripped,
   shuffled worksheet. It **refuses to run unless the first pass is
   complete** — any `gold: null` is a controlled failure, because a blind
   relabel of a half-labelled set has nothing to compare against.
   `tools/label_diff.py` compares the two founder passes afterwards and
   likewise refuses incomplete input: null gold on either side, case-id
   sets that differ, duplicate ids, or two facts in one case that share
   an alignment key (which would silently drop a real disagreement). All
   of these fail closed with exit 2 and no report. Disagreements become
   worked examples (schema §10), also kept private until the founder
   chooses to generalise one.
4. Founder-maintained, append-only in the private workspace:
   `ambiguity-log.md`.

## Current repository state (2026-08-14)

- Shipped worked examples in repo: 3 (`dataset.sample.jsonl`).
- Reference / synthetic / real / held-out: **not in this repository, by
  policy.** Their counts are reported from the private workspace at
  checkpoint time and are 0 in-repo by design.
- Baseline v0.1: not started. Eligible labelled HELD-OUT cases: 0/30
  (development/legacy/reference cases do not count toward this figure,
  whatever their number).
