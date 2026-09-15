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

**2 — Implement.** Stay inside the brief's `allowed_paths`. Choose the
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
outputs, and records one verdict for the round. Then **read the raw files
yourself** — the script only extracts the verdict line, and the findings
channel carries findings with no verdict at all.

**5 — Decide, per finding.** For each thing the reviewer reports:

- **Real and in scope** → fix it. Then return to step 3.
- **Not real** → refute it with evidence: a test that passes, a line of code
  that already handles it, a scenario that cannot occur. Write the refutation
  down. Changing code you believe is correct, to make a reviewer stop
  complaining, is the failure this step exists to prevent.
- **Real but out of scope** → do not fix it. Record it and go to *Stopping*
  if it blocks the package, otherwise carry it to the delivery as an open
  item.

**6 — Repeat** from step 3 until the round verdict is `approve`, or a limit
stops you. The script enforces one initial review plus at most N automatic
rounds (default 3) and a total time budget; both survive a session restart,
so resuming does not hand back spent rounds.

**7 — Deliver.** `python -m scripts.review_handoff finish --run-dir <dir>`
Refuses unless a usable `approve` describes the **current** tree. Then open a
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

## What never counts as a pass

A plugin call that failed. A missing or empty result. Output with no verdict
line, or two verdicts that disagree. A round where one channel did not
complete. A verdict that describes an older tree. A failing verification
command. In every case the script exits 1 and says why — treat that as the
answer, not as an obstacle.

## Fixtures

`fixtures/` holds one reviewer response per handled condition, with the
action each must produce, and `tests/test_review_handoff.py` asserts those
actions (ADR-001 §5). The tests assert **what the workflow does**, never how
the reviewer phrased itself.

This block supplements, not replaces, the repository's Response Packet Rule.
