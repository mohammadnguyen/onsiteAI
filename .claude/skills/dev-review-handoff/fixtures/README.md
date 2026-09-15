# Fixtures — known reviewer outputs with the action they must produce

ADR-001 §5: a skill that has never caught a planted flaw is decoration. These
files are the planted flaws. Each is a reviewer response this workflow has to
handle, and `tests/test_review_handoff.py` asserts the **action the workflow
takes** for each one — proceed, block, or stop.

Two shapes, because the plugin has two:

* **`.json`** — what the plugin really prints under `--json`, which is how it
  is always called. The adversarial channel is run against the plugin's own
  output schema (`schemas/review-output.schema.json`), so its payload carries
  a `result` object with a verdict and per-finding severities. Those are
  **read mechanically** and they gate release. The native channel's payload
  carries no `result` at all: its review is prose in `codex.stdout`.
* **`.txt`** — bare prose, the fallback path for a plugin build that returns
  no payload. Only an explicit `Verdict:` line is read there.

What is deliberately *not* asserted anywhere is reviewer wording. Severities
come from the plugin's own schema; everything else is recorded by the agent
as an explicit act. Building a classifier over model prose would put a second
stochastic component where a gate is supposed to be.

## Structured payloads (`--json`, the normal path)

| File | Plants | Required action |
|---|---|---|
| `structured-approve.json` | a clean adversarial result | proceed — may finish as reviewed |
| `structured-blocking.json` | one `high` finding | record it; must NOT finish until it is fixed or refuted |
| `structured-out-of-scope.json` | a finding marked `OUT-OF-SCOPE` | record as awaiting adjudication, do NOT implement, stop and ask |
| `structured-malformed.json` | a severity outside the schema's four words | block — the round is unusable, and the channel is NOT attested as read |
| `structured-missing.json` | `result: null` with a `parseError` | block — no structured result and no verdict line is not a pass |
| `native-empty.json` | the native channel completing with no review text | block — the channel produced nothing, so the round is incomplete |
| `native-prose.json` | a real native review | the round may proceed; its findings are triaged by the agent |

`native-empty.json` is the subtle one. The plugin exits 0 in that state and
renders it as "Codex review completed without any stdout output", and the
`--json` envelope around it is still a few hundred characters — so anything
measuring the raw output rather than `codex.stdout` counts the wrapper as a
review and lets a channel that said nothing pass for a complete one.

## Bare prose (no payload — the fallback path)

| File | Plants | Required action |
|---|---|---|
| `approve.txt` | a clean adversarial review | proceed — may finish as reviewed |
| `needs-attention.txt` | one grounded finding | proceed to the fix step; must NOT finish |
| `no-verdict.txt` | a review with no verdict line | block — unusable, never a pass |
| `conflicting.txt` | two verdicts in one response | block — unusable, never a pass |
| `empty.txt` | an empty response | block — unusable, never a pass |
| `native-clean.txt` | the findings channel, no machine verdict | archived; contributes findings only |

The findings channel (`codex review`) never carries a verdict, so it can
never gate on its own. It still has to *run*: a round where it did not
complete is an incomplete review, and an incomplete review is not a pass.
