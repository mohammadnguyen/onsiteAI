# Isolated samples

One reviewer output per condition the procedure has to handle, saved so the
handling can be checked without calling the reviewer and without a repository to
review. Nothing here executes; they are read by hand against `../SKILL.md`.

Every sample is shaped like the real thing: the envelope keys, the exit codes and
the failure strings are those the installed plugin actually emits. The content is
invented.

| Sample | Condition | Expected handling |
|---|---|---|
| `01-adversarial-findings.json` | Structured channel returns `needs-attention` with three findings: one high, one low, one prefixed `OUT-OF-SCOPE` | Usable. The high finding blocks. The out-of-scope one blocks **at any severity** and is never fixed here — record it and stop for authorisation. The low one is reported, does not block. |
| `02-native-envelope.json` | Prose channel's envelope, its body carrying one defect the structured channel did not report | Usable. Must be **read**; nothing classifies it. Its defect is accounted for separately, and an `approve` from the other channel would not release it. |
| `03-failure-not-an-envelope.txt` | `stdout` is prose, not JSON | **Not a pass.** `json.loads` fails, so there is no verdict. Do not read a verdict out of it, do not look for an embedded object, do not read stderr for one. One unusable result; a second consecutive one is a stop condition. |
| `04-failure-turn-incomplete.json` | Exit 1, `codex.status` 1, `result` null, `parseError` a JSON syntax error over a planning sentence | **Not a pass.** The turn did not complete. The preamble in `stdout` is not a review and not evidence the model "only planned". Record the failure verbatim; the cause may not be in the envelope at all. |
| `05-budget-exhausted.md` | The round budget runs out with a blocking finding still open | **Stop and report.** The package is delivered as Draft with the open item named. Do not start a fresh package to buy back rounds; do not treat "no rounds left" as an approval. |

## What these do not cover

They are inputs, not a harness. Walking them proves the procedure has an answer
for each condition and that the answer is written down. It does not prove a
future session will follow it — nothing in this design enforces that, and the
skill says so.
