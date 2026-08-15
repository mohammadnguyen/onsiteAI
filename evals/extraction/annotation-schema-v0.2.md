# Extraction Annotation Schema v0.2

**Status:** CURRENT — new corpora are authored against this version.
**Predecessor:** `annotation-schema-v0.1.md` is the historical contract;
its rules are unchanged (the file carries only an added historical-status
banner), and existing v0.1 datasets remain valid v0.1 datasets, not
reinterpreted. A corpus is validated as v0.2 **iff** it carries a
v0.2 corpus manifest (§2); otherwise the validator applies v0.1 rules.

**Bindings:** DEC-ONTOLOGY-001 (three top-level Candidate types + Amendment
10 fact-category ruling), DEC-JOB-ATTR-001 (Job is explicit human context),
DEC-GATE-H2-001 (wrong-Job suggestion = product telemetry), DEC-LANG-001,
DEC-EVIDENCE-001, schema-v0.1 §6 storage policy (real material never enters
the public repository — unchanged and still binding).

v0.2 changes exactly four things: a **corpus provenance manifest**, an
explicit **job-context state**, an optional **fact_category** on Site Log
Facts, and a **modality** field that stops overloading `meta.source`.
Everything not restated here (types, support levels, gold fact objects,
labeling procedure, privacy scrubbing, corpus composition) is inherited
from v0.1 unchanged.

---

## 1. Unit of evaluation

Unchanged from v0.1: one capture = one utterance + frozen context. One Raw
Evidence item may produce **zero or more** atomic Site Log Facts, zero or
more Tasks and zero or more Potential Variations.

## 2. Corpus provenance manifest (REQUIRED for v0.2)

Every v0.2 corpus `<name>.jsonl` has a sidecar `<name>.manifest.json`:

```json
{
  "schema_version": "0.2",
  "event_origin": "real | synthetic | unknown",
  "creation_method": "contemporaneous_capture | retrospective_reconstruction | constructed_example",
  "verbatim_capture": true,
  "ai_exposure": "none | raw_seen | gold_seen",
  "intended_use": "reference | development | heldout"
}
```

`verbatim_capture` is `true`, `false`, or the string `"unknown"`.

Rules:

- **Corpus-level, homogeneous.** All five provenance fields are declared
  once per corpus and apply to every record. There are **no record-level
  provenance overrides** in v0.2 — heterogeneous material must be split
  into separate corpora. This keeps development and held-out data out of
  one file, prevents silent leakage, and keeps the path policy and
  Baseline gates deterministic.
- **Declared provenance is authoritative.** The filesystem path remains a
  storage/security constraint and must **agree** with the declaration
  (§2.1), but a filename alone never establishes provenance.
- **Public-repository rule:** `event_origin: real` is forbidden for any
  corpus inside the public repository. (The v0.1 `founder_voice` check
  remains as defence-in-depth for legacy files; it is not the provenance
  model.)

### 2.1 Path–declaration agreement

| Storage class (path) | Required `intended_use` |
|---|---|
| public `dataset.sample.jsonl` / `calibration/synthetic.jsonl` | `reference` |
| private `reference.jsonl` | `reference` |
| private `dataset.v0.jsonl` | `development` |
| private `dataset.heldout.jsonl` | `heldout` |

A mismatch is a validation **error**, not a warning.

### 2.2 `ai_exposure` and the Held-out lifecycle

`ai_exposure` records AI exposure during **corpus construction,
annotation, schema/prompt development and pre-evaluation preparation**.
Running the frozen corpus through the model under evaluation does **not**
mutate the corpus manifest (no automatic manifest mutation exists); every
run instead records dataset version, prompt version and model version in
its `run-meta.json`, and independence is judged against that record.

A held-out corpus is valid for a given run only if it was not used to
design or tune that run's prompt/model configuration. Once humans inspect
its failures and use them to tune a later configuration, it is no longer
independent Held-out evidence for that later configuration — later
independent Baseline claims require a fresh held-out corpus or a clearly
pre-registered untouched partition. Cross-provider comparison on one
held-out corpus is valid only when the compared configurations are fixed
before any results are inspected; sequential tuning against the same
held-out failures is not a Held-out comparison.

### 2.3 `ai_exposure` — canonical enum and historical mapping

`ai_exposure` is the single canonical field. Historical private corpus
manifests that used the boolean pair `ai_raw_exposed` / `ai_gold_exposed`
remain untouched as frozen records; they map as documentation only:

| historical flags | canonical value |
|---|---|
| `ai_raw_exposed: true`, `ai_gold_exposed: false` | `raw_seen` |
| `ai_gold_exposed: true` (regardless of raw flag) | `gold_seen` |
| both `false` | `none` |

The booleans are **not** accepted as a v0.2 manifest; the mapping exists so
historical manifests can be read unambiguously, never as an override.

`verbatim_capture` is type-strict: JSON boolean `true`, JSON boolean
`false`, or the string `"unknown"`. Integers `0`/`1`, `null` and the
strings `"true"`/`"false"` are invalid.

## 3. Record line format (v0.2)

```json
{
  "id": "R-0001",
  "utterance": "<verbatim transcript, privacy-scrubbed>",
  "lang": "en | zh | mixed",
  "context": {
    "reference_time": "2026-08-15T16:40:00+10:00",
    "job_state": "confirmed | unassigned",
    "job": {"name": "JOB-A (18 Example St)"},
    "people": [], "suppliers": [], "locations": [], "notes": ""
  },
  "gold": null,
  "meta": {
    "modality": "text | voice_transcript | photo | document | other",
    "collected_at": "2026-08-15",
    "privacy": "scrubbed",
    "routing_case": "multi_job"
  }
}
```

### 3.1 Job context (`context.job_state`, required)

Product contract this encodes (documentation of founder-approved rules —
no production implementation is authorized by this schema):

- Raw Evidence may exist without a confirmed Job.
- Project Truth requires a human-confirmed Job.
- Capture launched from a Job page, or a Job manually selected by the
  user, is human-confirmed context. Global Quick Capture may remain
  unassigned (Unassigned Inbox).
- AI may **suggest** a Job (alias, assignment, GPS, schedule, recent
  context) but never silently assigns or reassigns one; a suggested Job is
  not Project Truth and is **never extractor input** — which is why the
  extraction context has exactly two states and no `suggested`.
- Wrong-Job suggestion remains product telemetry (DEC-GATE-H2-001), not an
  extraction metric.
- Production `Evidence.job_id` keeps its existing rule: nullable; non-null
  means human-confirmed only. No `job_assignment_status` column, enum or
  migration exists or is added by this contract.

Cross-rules (validated):

```text
job_state = confirmed  => context.job MUST be present (non-null name)
job_state = unassigned => context.job MUST be absent or null
```

Legacy v0.1 records (job present, no `job_state`) remain valid **as v0.1**
confirmed-context cases; they are not rewritten.

### 3.2 Multi-Job routing (`meta.routing_case`, optional)

Multi-Job Raw Evidence must never be forced into one Job. v0.2 deliberately
does **not** add `context.jobs` or per-fact Job assignment; that is a
deferral, not permission to pick one Job. A capture spanning several Jobs
is routed `meta.routing_case: "multi_job"` with `job_state: "unassigned"`,
is **excluded from the single-Job extraction Baseline**, and requires later
human splitting/assignment before any Project Truth. Such multi-Job cases
stay reference/routing material only.

### 3.3 Modality (`meta.modality`, required in v0.2)

`text | voice_transcript | photo | document | other`. Modality is how the
evidence was captured; provenance comes from the corpus manifest, never
from modality. v0.1's `meta.source` is not redefined — v0.1 fixtures using
`meta.source: synthetic_fixture` remain valid under v0.1.

## 4. Gold fact objects and `fact_category`

Gold fact objects are inherited from v0.1 §4 (type, summary, attrs with
support levels). v0.2 adds one optional field on **site_log_fact only**:

```json
{"type": "site_log_fact", "fact_category": "delivery", "summary": "...", "attrs": {}}
```

Closed list:

```text
attendance | progress | site_condition | quality | delivery | inspection |
safety | delay | incident | instruction | weather | other
```

Rules (DEC-ONTOLOGY-001 Amendment 10):

- `fact_category` classifies an atomic Site Log Fact. It is **not** a
  Candidate type; the top-level ontology remains exactly
  `site_log_fact | task | potential_variation`.
- It creates no workflow, module or additional confirmation object.
- A complex capture is split into atomic facts, each with its own
  category, rather than given a compound category.
- Generic `issue` is not a category (quality/safety/delivery/delay issues
  would overlap).
- `fact_category` on a `task` or `potential_variation` is a validation
  error.
- Expense and Labour remain outside this ontology entirely.

## 5. Baseline eligibility (corrects the earlier development-based draft)

**Independent Baseline eligibility** requires ALL of:

```text
event_origin:     real
creation_method:  contemporaneous_capture
verbatim_capture: true
ai_exposure:      none
intended_use:     heldout
```

- `development` corpora are for schema and prompt calibration
  (**development-ready**); they may never support claims about performance
  on unseen data and contribute **zero** cases to the Baseline gate.
- `raw_seen`, `gold_seen`, `retrospective_reconstruction`,
  `constructed_example` and `synthetic`/`unknown` origins likewise
  contribute zero.
- Legacy data without a v0.2 manifest may validate structurally (with
  warnings) but contributes **zero** cases to v0.2 Baseline readiness —
  this is a hard exclusion, not a warning.
- `multi_job`-routed records are excluded from the count even inside an
  eligible corpus.
- The machine gate remains **structural**: it can verify the manifest and
  count; it cannot verify founder independence, the elapsed blind-relabel
  week, disagreement resolution or freeze. Baseline v0.1 still requires
  the founder's explicit confirmation of those human facts.

## 6. Everything else

Types and their definitions, support levels and their tests, privacy
scrubbing, storage policy (real material never in the public repo),
labeling procedure, blind relabel, ambiguity log, corpus composition and
worked examples: inherited from v0.1 unchanged.
