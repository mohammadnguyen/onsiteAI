# Running a development/review handoff

How to actually run one founder-approved work package end to end. The
decision behind it is ADR-003; the procedure the agent follows is
`.claude/skills/dev-review-handoff/SKILL.md`; the mechanics are
`scripts/review_handoff/`, tested by `tests/test_review_handoff.py`.

This workflow never merges and never deploys. It ends at a Draft PR.

## Prerequisites

- The reviewer plugin is installed and authenticated. Check with
  `node <plugin>/scripts/codex-companion.mjs status`. Authentication lives
  outside the repository and expires; a run cannot repair it and will stop.
- A branch in its own worktree, off the integration branch.
- An approved brief (template:
  `.claude/skills/dev-review-handoff/task-brief-template.md`).

## The five commands

Run from the repository root of the worktree.

```bash
python -m scripts.review_handoff start  --brief handoff-brief.toml
python -m scripts.review_handoff gate   --run-dir .claude/handoff/<run-id>
python -m scripts.review_handoff review --run-dir .claude/handoff/<run-id>
python -m scripts.review_handoff status --run-dir .claude/handoff/<run-id>
python -m scripts.review_handoff finish --run-dir .claude/handoff/<run-id>
```

`stop --run-dir <dir> --reason "…"` closes a run without a pass.

Exit codes: `0` proceed, `1` blocked (a limit, a stale verdict, a failing
gate, an unusable review), `2` misuse (bad arguments, invalid brief,
unreadable state). Exit 1 is the workflow doing its job; the reason is
printed and recorded.

## What each step guarantees

| Step | Guarantee |
|---|---|
| `start` | The base is the merge base with the integration branch, not HEAD. The brief is archived verbatim. Limits are fixed and can only be lowered from here. |
| `gate` | The brief's commands run in order, stop at the first failure, and are archived verbatim. Sequential is a correctness requirement: these suites share one database. |
| `review` | Both review channels run against the pinned base with `--wait`; both raw outputs are archived; one verdict is recorded for the round. |
| `status` | Whether a current pass exists, and whether an earlier approval has gone stale. |
| `finish` | Refuses unless a usable `approve` describes the current tree. Writes the evidence index. |

## Limits

Defaults: one initial review plus **3** automatic rounds, and **4 hours**
total. Both live in the brief's `[limits]` table; the command line may lower
them, never raise them. Both are stored in the run directory, so restarting
a session does not hand back spent rounds.

## Evidence

Everything lands in the run directory (`.claude/handoff/<run-id>/`):

```
run.json                       state, rounds, gate records
brief.toml / brief.json        the approved inputs, verbatim
gate-NN/gate-NN-*.log          one file per verification command
review-NN-adversarial-review.log   raw reviewer output (carries the verdict)
review-NN-review.log               raw reviewer output (findings, no verdict)
delivery.json                  the index, plus the run's own cost
```

That directory is **gitignored**, so it is not visible in the PR. Paste the
evidence index into the PR body.

## Failure modes and what to do

| Symptom | What it means | Action |
|---|---|---|
| `gate` exits 1 with a test failure | a real regression, or shared-database interference | read the archived log; a schema/type-cache storm across unrelated suites is usually another session running against the same database, so re-run serially — do not assume either answer |
| `review` exits 1, "no recognised verdict line" | the reviewer answered, but not in a form a machine may act on | read the raw file; if the content is a real review, act on it manually and record that the round was unusable |
| `review` exits 1, "the findings channel did not complete" | one channel failed, so the review is incomplete | retry once; twice in a row is a stop |
| `review` exits 1, "review exited N" | plugin or authentication failure | check `codex-companion.mjs status`; fix outside the run |
| `finish` exits 1, "describes an older tree" | the tree changed after the approval | re-run `gate` and `review` |
| `start` exits 2, "not the merge base" | the base would hide part of the package | drop `--base` and let it pin the merge base |

## Limits of this workflow

- **The reviewer is a language model.** Its approval is evidence, not proof.
  The repository's own gates are the deterministic part; the review is a
  second opinion that has caught real defects and has also reported things
  that were not defects.
- **Only one channel carries a machine-readable verdict.** The other is run
  and archived because it has caught defects the first missed, but nothing
  classifies its prose.
- **The agent judges its own fixes** between rounds. That is the autonomy
  being trialled, and ADR-003's kill criterion is how it gets withdrawn.
- **CI cannot run a real review** — the plugin needs credentials CI does not
  have. The tests use an isolated fake; a real call is exercised by hand and
  archived with the run that made it.
