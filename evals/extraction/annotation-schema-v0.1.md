# Extraction Annotation Schema v0.1

**Governs:** `evals/extraction/dataset*.jsonl`
**Bindings:** DEC-ONTOLOGY-001 (three types only), DEC-GATE-H2-001 (corpus mix + metrics), DEC-LANG-001 (pure English mandatory), DEC-EVIDENCE-001 (raw evidence preserved).
**Scoring in v0.1 is manual.** No automated scorer, no LLM judge. Failures are classified by type, never aggregated into a weighted score (sample sizes are too small for aggregate numbers to mean anything).

## 1. Unit of evaluation

One **capture**: a single utterance (voice transcript or typed note) plus its **frozen context**. Context is frozen at labeling time so the case remains stable as the product's live context changes.

## 2. Dataset line format (JSONL, one object per line)

```json
{
  "id": "S-0001",
  "utterance": "<verbatim transcript, privacy-scrubbed>",
  "lang": "en | zh | mixed",
  "context": {
    "reference_time": "2026-08-05T16:40:00+10:00",
    "job": {"name": "JOB-A (18 Example St)"},
    "people": [{"name": "John", "trade": "plumbing"}],
    "suppliers": ["Reece"],
    "locations": ["First Floor Ensuite"],
    "notes": "optional free-text context the builder had at the time"
  },
  "gold": {
    "facts": [ <gold fact objects, see §4> ],
    "must_not_infer": [ "<assertions the extractor must NOT produce>" ]
  },
  "meta": {"source": "founder_voice", "collected_at": "2026-08-05", "privacy": "scrubbed"}
}
```

`context.job` is the **user-confirmed authoritative Job** (DEC-JOB-ATTR-001): the extractor consumes it and never predicts it.

`gold.facts` may legitimately be an **empty array** — mundane captures with nothing actionable are a required part of the corpus (they test unsupported-inference behaviour). `must_not_infer` lists the tempting over-extractions for this specific case.

## 3. Types (closed list — DEC-ONTOLOGY-001)

- `site_log_fact` — a statement of what happened / what is the case (includes dependencies and blockers: "waterproofing can't start until X" is a site_log_fact, not a separate type).
- `task` — someone needs to do something. Requires a directive or accepted commitment, not merely a stated plan.
- `potential_variation` — a possible scope/commercial change signal. A client *request or question about changing scope* qualifies; the extractor flags, the builder determines (DEC-AI-BOUNDARY-001). Never includes pricing or approval.

## 4. Gold fact object

```json
{
  "type": "task",
  "summary": "<one-line human summary of the fact>",
  "attrs": {
    "person":   {"v": "John",        "support": "explicit"},
    "trade":    {"v": "plumbing",    "support": "reasonable"},
    "due":      {"v": "2026-08-06",  "support": "explicit"},
    "location": {"v": "ensuite",     "support": "explicit"}
  }
}
```

Attribute keys are free within reason in v0.1 (person, trade, due, location, requester, change, dependency, status…). What is **not** free is the support level on every attribute:

## 5. Support levels (the core of this schema)

- `explicit` — stated in the utterance ("John", "tomorrow", "加长200").
- `reasonable` — a competent site person with this frozen context would infer it (John→plumbing given the roster; "tomorrow" resolved against reference_time).
- `unknown` — genuinely not determinable; gold records the attribute with `"v": null` when its absence matters (extractor must not fill it).
- `ambiguous` — two+ readings survive context; gold records the accepted readings and the case becomes a worked example.

The whole eval lives on the line between `reasonable` and unsupported invention. General test: *would a competent builder, given this utterance and this frozen context, accept this attribute into the project record without first asking a clarifying question?* If a clarifying question would be needed, the attribute is `unknown` or `ambiguous`, not `reasonable`.

Stricter commercial standard: for `potential_variation` facts and any attribute with commercial or contractual implications, apply the harder test — *if you would not rely on the inference when reviewing a real Variation, it is unsupported.*

## 6. Privacy

Before an item enters the repo: client and subcontractor real names replaced with consistent pseudonyms (one mapping per real person, mapping file kept **outside** the repo); dollar amounts removed or rounded to bands if commercially sensitive; addresses genericised. Dimensions and technical values stay (they are what extraction is for).

## 7. Corpus composition (per DEC-GATE-H2-001)

Every batch must include: pure English; Chinese/mixed; shorthand and trade/supplier names; and deliberately mundane captures with empty gold. Do not curate only "interesting" failures — that biases every number.

## 8. Labeling procedure

1. **Calibration set (10–20 items):** founder labels alone using this schema. This set exists to test the schema — the reasonable-vs-unsupported line, worked examples — and produces no baseline metrics. Every hesitation goes into an ambiguity log (case id + what was unclear).
2. One week later: shuffle order, re-label blind, diff. Each disagreement becomes a **worked example** appended to §10 with the ruling.
3. Schema changes bump the version (v0.2, …) and note which cases were labeled under which version.
4. **Baseline v0.1 minimum: 30+ labelled samples.** Calibration items count toward the 30 once re-labelled consistently. Later expansions: 50, 100, …

## 9. Manual scoring protocol (v0.1)

For each case, compare model output to gold and tally:

- per gold fact: `captured` / `missed` / `captured-wrong-type`
- per model fact with no gold counterpart: `hallucinated`
- per attribute on captured facts: `attr-wrong` with subtype (`person`, `trade`, `time`, `location`, `job`)
- flag separately: any `missed` fact that is commercially important (PV misses top the list)

Job attribution is **not scored** in this eval: `context.job` is user-confirmed input per DEC-JOB-ATTR-001; wrong-Job **suggestion** rate belongs to capture attribution telemetry (H1), not the extraction baseline.

Report counts by failure type, plus PV recall/precision as raw fractions (e.g. "PV 5/7 captured, 1 hallucinated"). No weighted totals.

## 10. Worked examples

(Seeded from the sample dataset; grows via the blind-relabel diff.)

- **Plan statement vs task.** "Sparky starts rough-in Monday" — site_log_fact (schedule statement), *not* a task: no directive was given to anyone. Contrast: "John needs to arrange flashing tomorrow" — task.
- **Client question vs instruction.** "Client asked if we could add two GPOs, said I'd price it" — potential_variation with `change: explicit`; `must_not_infer`: client approval, that the work is proceeding. A question is enough to flag PV; it is never enough to infer approval.
- **Dependency folding.** "Waterproofer can't start until the waste is moved" — one site_log_fact with a `dependency` attribute; do not invent a Delay type (Ontology v2+).

## 11. Explicitly not in v0.1

Progress/Rework/Delay/EOT labels; automated scoring; LLM-as-judge (requires fixtures first); per-user personalization signals; weighted aggregate quality scores.
