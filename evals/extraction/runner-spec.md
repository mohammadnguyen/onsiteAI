# Baseline Runner Spec (v0)

**Purpose:** produce the first ugly numbers. The runner's job is to make model output easy to hand-score against gold, per annotation-schema-v0.1 §9. Claude Code implements this; keep it boring.

**Data location (binding, founder ruling 2026-08-14):** this repository is public, so it never holds a real utterance, real gold label, model output over real data, or a scoring report. The runner reads the real dataset from the private workspace (`--private-root` / `$FOREY_PRIVATE_CALIBRATION`, outside every registered Git worktree) and writes every artefact back there. The same `path_policy.py` guard the calibration tools use applies to the runner: fail closed, exit 2, no partial output. Public synthetic fixtures may stay in the repo for smoke tests but never count toward a real baseline.

**Provider neutrality (required):** the eval framework defines the I/O contract only and must not pre-decide the product's model vendor. Provider and model are run-time configuration, and comparing the same dataset across providers is an explicit design goal of this harness.

## Behaviour

1. Read `<private-root>/dataset.v0.jsonl` (labelled cases only — skip items with `"gold": null`). Refuse any dataset path inside a registered worktree. A run named "baseline" requires ≥30 labelled cases AND the founder's explicit confirmation that independent labelling, the one-week blind relabel, disagreement resolution and freeze are complete — `--baseline-structure-ready` checks only the structural minimum and never substitutes for that confirmation. Smaller or unconfirmed runs are calibration smoke tests and must be named as such.
2. For each case, call the configured model **once** with the deliberately naive v0 prompt:
   - System: you extract structured site facts for an Australian residential builder. Output JSON only: `{"facts":[{"type":"site_log_fact|task|potential_variation","summary":...,"attrs":{...}}]}`. Extract only what the utterance and provided context support. If nothing is actionable, return `{"facts":[]}`.
   - User: the frozen `context` object + the `utterance`, verbatim.
   - No few-shot examples, no schema hints beyond the above, temperature 0. That's the point of a baseline.
   - Note: the `job` in context is the user-confirmed Job (DEC-JOB-ATTR-001). The model consumes it; it does not predict it, and Job attribution is not scored by this eval.
3. Write `<private-root>/results/<YYYY-MM-DD>-baseline/outputs.jsonl` — one line per case: `{id, gold, model_output, error?}`. This file contains real gold and model output over real utterances, so it is private by construction and must never be written inside a worktree.
4. Write `run-meta.json` in the same folder (mandatory): `{provider, model, model_version_or_snapshot, prompt_version, dataset_version, sample_count, temperature, retries_used, token_usage (est/actual where available), api_cost (est/actual where available), run_date}` — cost fields exist so future model comparisons weigh accuracy AND cost.
5. Generate `scoring_sheets.md`: one section per case showing utterance, context summary, gold facts as a checklist, model facts as a checklist, and blank tally lines (`captured / missed / hallucinated / attr-wrong: person|trade|time|location`).
6. Never write anything outside `<private-root>/results/`, and never write anything inside a registered worktree.

## Config

```
EVAL_PROVIDER=<provider id>        # e.g. anthropic | openai | ...
EVAL_MODEL=<model id>
<provider-specific API key env>
```

One thin `call_model(system, user) -> text` adapter per provider; adding a provider must not touch the harness logic. Retries: 2 with backoff. Batch budget: support `--limit N` and an inter-call delay (env `EVAL_CALL_DELAY_MS`, default 0); a full baseline run is ~30–50 calls. Malformed JSON from the model is recorded as `error`, not repaired (a baseline that needs repair is itself a finding).

## After scoring

Founder tallies by failure type and writes `<private-root>/results/<...>/baseline-001.md` (private — it quotes real utterances and gold), opening with the run-meta block verbatim, then counts per type, PV recall/precision as raw fractions, and one reading note per failure case. Those notes — not the fractions — are the input to the next strategy conversation (ADR-002 re-entry bar). Cross-provider baselines are compared per failure type against the same dataset version, never as a single blended score.
