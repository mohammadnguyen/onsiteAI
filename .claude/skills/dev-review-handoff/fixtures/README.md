# Fixtures — known reviewer outputs with the action they must produce

ADR-001 §5: a skill that has never caught a planted flaw is decoration. These
files are the planted flaws. Each is a reviewer response this workflow has to
handle, and `tests/test_review_handoff.py` asserts the **action the workflow
takes** for each one — proceed, block, or stop.

## The call contract

Every review channel is invoked with `--json`, so **every channel owes a JSON
envelope**. That is the only shape a reviewer answer can legitimately take
here. Output in any other shape — prose, a truncated object, a banner
followed by a broken object — is a refusal, not a dialect. There is no text
mode and no "looks like JSON" test, because those are exactly how a
non-conforming answer gets read as an approval.

Inside a valid envelope:

* the **adversarial** channel's `result` is validated against the plugin's
  own output schema (`schemas/review-output.schema.json`) and its verdict is
  taken from that object's enum — `approve` or `needs-attention`, nothing
  else;
* the **native** channel carries its review in `codex.stdout`. That text is
  read so the agent can triage it, and is never classified.

Nothing is ever read from stderr but a diagnostic for an already-failed call.

## Valid envelopes

| File | Plants | Required action |
|---|---|---|
| `structured-approve.json` | a clean adversarial result | proceed — may finish as reviewed |
| `structured-blocking.json` | one `high` finding | record it; must NOT finish until it is fixed or refuted |
| `structured-out-of-scope.json` | a finding marked `OUT-OF-SCOPE` | record as awaiting adjudication, do NOT implement, stop and ask |
| `structured-malformed.json` | a severity outside the schema's four words | block — the round is unusable, and the channel is NOT attested as read |
| `structured-missing.json` | `result: null` with a `parseError` | block — an envelope without a conforming result is not a review |
| `structured-bad-verdict.json` | a verdict outside the protocol's enum | block — the verdict is not guessed at |
| `native-empty.json` | the native channel completing with no review text | block — the channel produced nothing, so the round is incomplete |
| `native-prose.json` | a real native review | the round may proceed; its findings are triaged by the agent |

`native-empty.json` is the subtle one. The plugin exits 0 in that state and
renders it as "Codex review completed without any stdout output", and the
`--json` envelope around it is still a few hundred characters — so anything
measuring the raw output rather than `codex.stdout` counts the wrapper as a
review and lets a channel that said nothing pass for a complete one.

## Non-conforming output

| File | Plants | Required action |
|---|---|---|
| `non-conforming-approval.txt` | a banner, a truncated object, then `Verdict: approve` | block — no verdict is taken from output that is not an envelope |

This one is the reason the text mode was removed. It does not begin with
`{`, so a first-character test classified it as a plugin that never emits
JSON, and the prose reader then took the approval line at the bottom. The
call asked for JSON; this is not JSON; there is nothing further to decide.
