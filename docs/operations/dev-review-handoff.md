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

## The commands

Run from the repository root of the worktree.

```bash
python -m scripts.review_handoff start  --brief handoff-brief.toml
python -m scripts.review_handoff gate   --run-dir .claude/handoff/<run-id>
python -m scripts.review_handoff review --run-dir .claude/handoff/<run-id>
python -m scripts.review_handoff status --run-dir .claude/handoff/<run-id>
python -m scripts.review_handoff finish --run-dir .claude/handoff/<run-id>
```

Between `review` and `finish`, account for every finding:

```bash
python -m scripts.review_handoff findings list    --run-dir <dir>
python -m scripts.review_handoff findings record  --run-dir <dir> --round 1 \
    --channel review --severity high --title "..." --file path --line 42
python -m scripts.review_handoff findings none    --run-dir <dir> --round 1 \
    --channel review --note "read the archived prose; nothing actionable"
python -m scripts.review_handoff findings resolve --run-dir <dir> --id r01-adve-1a2b3c4d \
    --disposition fixed --note "commit abc1234 plus its regression test"
```

`stop --run-dir <dir> --reason "…"` closes a run without a pass.
`--linked-run <dir>` on `start` records that this run continues an older
one; the older run is not modified.

Exit codes: `0` proceed, `1` blocked (a limit, a stale verdict, a failing
gate, an unusable review), `2` misuse (bad arguments, invalid brief,
unreadable state). Exit 1 is the workflow doing its job; the reason is
printed and recorded.

## What each step guarantees

| Step | Guarantee |
|---|---|
| `start` | The base is the merge base with the integration branch, not HEAD. The brief is archived verbatim. Limits are fixed and can only be lowered from here. |
| `gate` | The brief's commands run in order, stop at the first failure, and are archived verbatim. Sequential is a correctness requirement: these suites share one database. |
| `review` | Refuses a dirty work tree: the reviewer reads the commits `base..HEAD` and would never see uncommitted work. Both review channels then run against the pinned base with `--wait --json`; both raw outputs are archived; the adversarial channel's structured findings are recorded with their severities; one verdict is recorded for the round. |
| `findings` | Every finding carries a disposition. Blocking ones stop delivery until fixed or refuted, whichever channel raised them. |
| `status` | Whether a current pass exists, whether an earlier approval has gone stale, and what is still blocking release. |
| `finish` | Refuses unless a usable `approve` describes the current tree, every channel was triaged, and no blocking finding is open. Writes the evidence index. |

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
| `review` exits 1, "uncommitted changes" | the reviewer reads commits only, so that work would be invisible to it | commit it, then re-run `gate` and `review` |
| `review` exits 1, "round limit reached" or "time budget exhausted" | the run is now **closed**, with the limit as its stop reason | report what is outstanding; do not start a fresh run to buy back rounds |
| any command exits 1, "is held by pid N" | another command owns this run, or another run owns the shared database | wait, or stop. **Never kill the holder** — stopping another session or shared runtime is outside this workflow's authorisation. If the holder really crashed, `--break-lock` (run lock) or `--break-shared-lock` (database lock); the break is recorded |
| `review` exits 1, "no structured result" | the reviewer did not answer against the plugin's own schema | read the raw file; retry once; twice in a row is a stop |
| `finish` exits 1, "unaccounted for" | a channel produced a review nobody read, or its outcome was never recorded | read the archive, then `findings record` or `findings none`; say in the note which of the two it was |
| `finish` exits 1, "not verifiable" | the evidence a delivery would rest on has no recorded digest (an older run) | re-run `gate` and `review` on this head; nothing back-fills a digest |
| `review` exits 1, "no usable structured result" | the reviewer's result does not match the plugin's protocol | read the raw file; retry once; twice in a row is a stop |

## Concurrency

`gate` holds a machine-wide lock named by the brief (`limits.shared_lock`,
default `shared-test-database`), so two runs never verify against the same
PostgreSQL instance at once; a second run queues. Every command that changes
an existing run holds that run's own lock — `start` does not, since there is
no run yet. A held lock is reported with its holder and never forced.

A command's subprocess tree is contained before it runs — a job object on
Windows (the child is created suspended and resumed once it is enrolled),
the process group on POSIX — and is torn down with it, on timeout **and** on
normal exit. Both mechanisms reach a worker whose launcher has already
exited, which `taskkill /T` cannot. A command whose tree cannot be contained
is refused rather than run.

Every archived log is hashed when written and checked before the next review
and before delivery, because the run directory is excluded from the tree
fingerprint and its contents would otherwise be free to change. The evidence
a delivery rests on must carry a digest, not merely match one: a record that
cannot be checked cannot support a pass, and older runs reach a delivery by
re-running `gate` and `review`, never by back-filling a hash.

Evidence paths are absolute, so a run started with a relative `--run-dir`
still resolves from any other directory. Each review channel's outcome is
persisted before the next channel starts, so an interruption leaves what
already happened on the record rather than losing it.

## Limits of this workflow

- **The reviewer is a language model.** Its approval is evidence, not proof.
  The repository's own gates are the deterministic part; the review is a
  second opinion that has caught real defects and has also reported things
  that were not defects.
- **Only one channel returns a structured result.** Its verdict and its
  findings are read mechanically. The other is run and archived because it
  has caught defects the first missed, but nothing classifies its prose —
  you read it and record what you found.
- **The agent judges its own fixes** between rounds. That is the autonomy
  being trialled, and ADR-003's kill criterion is how it gets withdrawn.
- **CI cannot run a real review** — the plugin needs credentials CI does not
  have. The tests use an isolated fake; a real call is exercised by hand and
  archived with the run that made it.
