---
name: dev-review-handoff
description: Run one founder-approved work package end to end — implement, verify, hand the diff to the external reviewer, fix or refute what it reports, and deliver a Draft PR. Use only when a written brief exists and the founder has approved it. Never merges, never deploys.
---

# dev-review-handoff

This is a procedure, not a persona. The deterministic half lives in
`scripts/review_handoff/` and is tested in `tests/test_review_handoff.py`;
this file is the judgement half. Where the two disagree, the script wins —
it is the part that cannot talk itself into an answer.

## Preconditions

Refuse to start unless all of these hold. Say which one failed.

1. A brief file exists and the founder approved **that text**. Template:
   `task-brief-template.md` beside this file.
2. The work is on its own branch in its own worktree, off the integration
   branch.
3. Nothing in the brief requires a credential, permission or branch-protection
   change. Those are never in scope for this workflow.

## Procedure

**1 — Start.** `python -m scripts.review_handoff start --brief <file>`
Pins the review base to the merge base with the integration branch, archives
the brief verbatim, and fixes the round and time budgets. Read back the
printed base and limits; they are the contract for everything below.

**2 — Implement, and COMMIT.** Stay inside the brief's `allowed_paths`.
The reviewer is handed the commit range `base..HEAD`; uncommitted work is
invisible to it, so `review` refuses a dirty tree rather than produce an
approval describing code nobody read. Choose the
solution that is most correct, most maintainable, and most consistent with
how this repository already does things. Smallest diff is **not** the
criterion — a smaller change that leaves the design worse is the wrong
change. If the correct fix needs a path the brief does not allow, go to
*Stopping*.

**3 — Verify.** `python -m scripts.review_handoff gate --run-dir <dir>`
Runs the brief's verification commands in order and archives each one
verbatim. Commands run strictly sequentially: suites here share one database
and corrupt each other when run at once. Exit 1 means a command failed —
fix the cause. Never review on top of a failing gate, and never re-run a
gate hoping for a different answer without first understanding why it
failed; an infrastructure failure and a real regression look different in
the log and must be distinguished by reading it.

**4 — Review.** `python -m scripts.review_handoff review --run-dir <dir>`
Calls both review channels against the pinned base, archives both raw
outputs, and records one verdict for the round.

The adversarial channel returns a structured result (the plugin constrains
it with its own JSON schema), so its findings are recorded automatically
with their severities. The other channel returns prose and nothing reads it
for you. **Read both raw files yourself**, then account for the prose
channel explicitly — there is no third state between "found something" and
"found nothing":

    python -m scripts.review_handoff findings record --run-dir <dir> \
        --round N --channel review --severity high --title "..." \
        --file path --line 42 [--out-of-scope]
    python -m scripts.review_handoff findings none --run-dir <dir> \
        --round N --channel review --note "what you read, and where"

**5 — Decide, per finding.** Every finding needs a disposition, recorded
with the evidence behind it:

- **Real and in scope** → fix it, then
  `findings resolve --id <id> --disposition fixed --note "<commit or test>"`,
  and return to step 3.
- **Not real** → refute it with evidence: a test that passes, a line of code
  that already handles it, a scenario that cannot occur. Record it as
  `--disposition refuted --note "<the evidence>"`. Changing code you believe
  is correct, to make a reviewer stop complaining, is the failure this step
  exists to prevent.
- **Real but out of scope** → do **not** fix it. It is recorded as
  `awaiting-adjudication` and cannot be marked fixed. Go to *Stopping* and
  ask the founder for authorisation.

A blocking finding (critical or high, or anything marked out-of-scope) stops
delivery until it is fixed or refuted, **no matter which channel raised it**.
An `approve` on one channel does not release the other channel's findings.

**6 — Repeat** from step 3 until the round verdict is `approve`, or a limit
stops you. Reaching a limit **closes the run**: `review` records it as
stopped, with the limit as the reason, and a closed run stays closed. Report
what is outstanding rather than starting a fresh run to buy back rounds. The script enforces one initial review plus at most N automatic
rounds (default 3) and a total time budget; both survive a session restart,
so resuming does not hand back spent rounds.

**7 — Deliver.** `python -m scripts.review_handoff finish --run-dir <dir>`
Refuses unless a usable `approve` describes the **current** tree, every
channel of every usable round has been triaged, and no blocking finding is
still open. Then open a
**Draft** PR containing: what changed and why, the verification evidence,
every review round with its verdict, each refuted finding with its
refutation, and the open items. Paste the run's evidence index into the PR
body — the run directory is gitignored, so it is not otherwise visible to a
reviewer.

## Stopping

Stop, run `python -m scripts.review_handoff stop --run-dir <dir> --reason "…"`,
and report. Stopping is a first-class outcome, not a failure:

- the requirements conflict with each other or with a repository rule;
- the correct change needs scope the brief does not grant;
- a permission or credential would be required;
- the round or time budget is exhausted;
- the reviewer cannot be reached, or returns nothing usable twice in a row.

## Concurrency

Every command that changes an EXISTING run takes that run's lock, and `gate`
also takes a machine-wide lock because the verification suites of different
runs share one database. (`start` takes no run lock — there is no run yet.) If a command reports that a lock is held, **wait or stop**.
Never kill the holder: stopping another session or a shared runtime is
outside what this workflow is authorised to do. A lock genuinely left by a
crashed session is cleared with `--break-lock`, which records the break in
the run.

## What never counts as a pass

A plugin call that failed. A missing or empty result. No structured result
and no verdict line, or two verdicts that disagree. A structured finding
that cannot be read. A round where one channel did not complete. A verdict
that describes an older tree. A failing verification command. An untriaged
channel. An unresolved blocking finding on either channel. In every case the
script exits 1 and says why — treat that as the answer, not as an obstacle.

## Fixtures

`fixtures/` holds one reviewer response per handled condition, with the
action each must produce, and `tests/test_review_handoff.py` asserts those
actions (ADR-001 §5). The tests assert **what the workflow does**, never how
the reviewer phrased itself.

This block supplements, not replaces, the repository's Response Packet Rule.
