---
name: codex-review
description: Run an approved work package from implementation to Draft PR, with the installed Codex plugin as the reviewer. Use when the founder has approved a work package and the change is ready to be gated and reviewed. Not for unapproved work, and not for merging.
---

# Codex review handoff

Claude implements, gates, and calls the installed Codex plugin directly. There is
no controller program: every step below is an ordinary command you run and read.

**What this skill is and is not.** The limits here — round counts, time budgets,
stop conditions — are a procedure Claude follows and reports against. They are
**not** machine-enforced. Nothing counts rounds for you, nothing survives a
session ending, and nothing guarantees unattended operation across sessions. Say
so plainly whenever you report against them; never describe them as enforced.

Runs only on a work package the founder has already approved. This skill does not
merge, does not deploy, and does not start a pilot.

---

## Before you start

Write these down, in the PR description or your working notes, before the first
command:

| | |
|---|---|
| Work package | what the founder approved, in their words |
| Acceptance criteria | what "done" means |
| Allowed paths | what this package may touch |
| Prohibitions | what it may not do |
| Verification commands | the gate, below |
| Base | `git merge-base origin/main HEAD` — the SHA, written down |
| Round budget | 1 initial review + at most 3 fix-and-review rounds |
| Time budget | the elapsed wall-clock limit, and the clock time it expires |

`review-input-template.md` in this directory is the same list, shaped for handing
to the reviewer.

**The time budget is elapsed time, recorded before the package starts.** A
session that is interrupted does not renew it. On resuming, the first thing you
do is check the deadline and the rounds already used, and report both, before
running anything.

## Repository rules this procedure does not override

- **Gate tiers** (CLAUDE.md). Full gate — schema migrations, auth/security,
  destructive or irreversible actions, architecture changes, public API
  behaviour, PRODUCT.md binding content, significant new capabilities, major new
  dependencies, anything touching the extraction/confirmation pipeline — needs
  the plan-review skill and explicit founder approval **before** mutation. Light
  gate — maintenance, bounded bug fixes, tests, small UX repairs — runs the
  checks then **STOPs**. STOP is a hard turn end, not a suggestion.
- **PRODUCT.md conflicts.** If the work conflicts with a `DEC-*` decision: STOP,
  quote the conflicting decision, wait for the founder. Never adjudicate it here.
- **Never pass `--scope` to the drift check to turn a red gate green.** The
  active scope is read from PRODUCT.md's `Binding Scope:` line; `--scope` is an
  override only. Widening scope is a PRODUCT.md edit, which is itself full gate.
- **ADR rule.** Decisions about review workflows, AI orchestration, auth, sync,
  mobile architecture, queues, extraction or deployment are recorded as ADRs.
  Governance ADRs live in `docs/decisions/`; `docs/adr/` has its own 4-digit
  format and index.
- **Eval rule**, dormant until the extraction eval harness exists: after that,
  any change to extraction prompts, context injection or candidate schema
  re-runs the baseline eval and reports the numbers against the previous one.

---

## 1. Implement, and commit

Write the change. Then **commit it.**

The reviewer is handed the commit range `base..HEAD`; uncommitted work is
invisible to it. Reviewing a dirty tree produces an approval describing code
nobody read. Assert it before every review call:

```bash
git status --porcelain
```

Empty output, or do not proceed.

Smallest diff is **not** the criterion — a smaller change that leaves the design
worse is the wrong change. Judge on correctness first, then maintainability,
then fit with what this repository already does.

## 2. Gate

Run the full gate, **strictly sequentially**. The suites share one database and
corrupt each other when run at once.

Root, from the repository root:

```bash
ruff check evals/extraction/tools tests
python -m pytest tests/ -q
python -m pytest tests/test_check_decision_drift.py -q
python scripts/check_decision_drift.py --require-full-coverage
```

Backend, from `backend/`:

```bash
uv run ruff check app tests
uv run python -m pytest -q
```

Mobile, from `mobile/`, when the change touches it:

```bash
npx tsc --noEmit
```

The last two root commands must run from the repository **root** — the drift
check resolves `docs/product/PRODUCT.md` relatively and fails from `backend/`.
Backend pytest needs the local Postgres container (`sitetracker-db` on :5433);
if it is not running, say so and report the backend result as UNCONFIRMED rather
than inventing one.

Keep every command's output verbatim. It is the evidence you hand the reviewer
and paste into the PR.

**Never review on top of a failing gate, and never re-run a gate hoping for a
different answer without first understanding why it failed.** An infrastructure
failure and a real regression look different in the log and must be
distinguished by reading it.

## 3. Review — both channels, against the pinned base

Two channels, every round:

```bash
BASE=<the SHA you recorded before you started>   # never recomputed here
PLUGIN=~/.claude/plugins/cache/openai-codex/codex/1.0.6/scripts/codex-companion.mjs

node "$PLUGIN" adversarial-review --wait --json --base "$BASE" --scope branch -- "<review input>"
node "$PLUGIN" review            --wait --json --base "$BASE" --scope branch
```

- **Use the base you recorded before you started. Do not recompute it.**
  `git merge-base origin/main HEAD` is how you *found* it once; running it again
  each round is how the review shrinks. If `origin/main` absorbs part of this
  package between rounds, the new merge base excludes those commits and both
  channels then report on a smaller range while the report still claims the
  recorded one.
  Check the recorded base is still an ancestor before each round:

  ```bash
  git merge-base --is-ancestor "$BASE" HEAD && echo ok
  ```

  If that fails, the branch history moved under you. **Stop and reassess the
  review range** rather than picking a new base quietly.
- **Pass `--base` explicitly, always.** Without it the plugin picks its own base
  and the review silently shrinks to a slice of the change.
- **Flags and the review input are separate argv elements, never one string.**
  The plugin re-splits its arguments with a shell-like tokeniser when — and only
  when — it is handed a single element. Packing them together eats backslashes
  in Windows paths and apostrophes in requirements, and reads flag-like prose as
  options; the reviewer then gets paths that do not exist.
- **Only the adversarial channel takes review input.** The native `review`
  subcommand rejects focus text outright.
- `--wait` and `--scope` are inert for reviews and are sent for symmetry only.
  An explicit `--base` already forces branch mode, so the review is exactly the
  commit range `base..HEAD`.
- Save both raw outputs. A summary you wrote is not evidence.

**Why both channels.** In the package that motivated this workflow, the native
channel reported clean in four rounds where the adversarial channel found real
defects — and in one round the native channel found a defect the adversarial
channel never saw. Do not simplify this to one channel.

### Reading the result

Four checks, in order. **Parsing is the first of them, not the whole test** — an
envelope that parses can still carry a failed call, an absent result or a result
that does not conform.

1. **The call succeeded.** Process exit code 0 **and** `codex.status` 0. Exit 1
   or `codex.status` 1 means the turn did not complete, whatever else the
   envelope contains.
2. **It parses.** `stdout` parses as JSON and yields an object. No
   first-character check, no hunting for an object embedded in prose, no second
   mode for output that merely resembles JSON — those heuristics are how a
   non-conforming answer becomes an approval. **stderr is diagnostic only and
   never carries a verdict.**
3. **There is a result.** On the adversarial channel, `result` is present and
   non-null, `parseError` is null, and `result.findings` is an actual list —
   an absent list and an empty list are different answers. On the native channel
   there is no `result` key by design, and the check is that `codex.stdout`
   carries a body rather than being empty.
4. **It conforms to the protocol.** The structured result satisfies the plugin's
   own `review-output.schema.json` for the installed version: required fields
   present, types and enums as declared, `verdict` one of the values the schema
   permits. A field that is missing, null or the wrong type is a protocol
   violation, not a field to default. Read the schema in the installed plugin if
   you need to check a shape.

Fail any of the four and there is no verdict to read. Do not repair the gap.

The adversarial channel returns a structured `result` with a verdict and
findings. The native channel returns prose in `codex.stdout`; **read it
yourself** — nothing classifies it for you.

### Before and after every review call, check the version you reviewed

`target.baseRef` in the envelope proves only **which base you passed in**. It
says nothing about HEAD and nothing about the working tree, so it does not on its
own establish what was reviewed.

Record, and check yourself, immediately before and immediately after each call:

```bash
git rev-parse HEAD
git status --porcelain
```

The review describes `base..HEAD` only if the recorded base is still an ancestor,
HEAD is the same before and after, and the tree was clean at both points. If HEAD
moved or the tree was dirty, the result describes something other than what you
are about to claim it describes — discard it and call again on a settled tree.

### None of these is a pass

- output that is not a JSON envelope
- a structured result that does not satisfy the plugin's own schema
- a call that failed, or returned nothing
- an empty or missing result
- a channel whose outcome you do not know — unknown is not "found nothing"
- a round in which one channel did not complete
- a verdict describing an older tree: if the head or the working tree moved, the
  verdict no longer applies
- a failing verification command
- a channel you have not accounted for
- an unresolved blocking finding on **either** channel

### Account for both channels, in writing

Every channel of every round ends in exactly **one of three** recorded outcomes,
and they are recorded as three different things:

| Outcome | What it means | What you write |
|---|---|---|
| **Completed, findings** | All four checks passed and the channel reported something | Enumerate every finding, with its severity and location |
| **Completed, no findings** | All four checks passed and the channel reported nothing | State that you read it and it reported nothing — name the channel and the round |
| **Failed or unknown** | Any of the four checks failed, or the call never returned, or the session ended before the outcome was recorded | Record the failure verbatim with its exit code and whatever the envelope said. **This is not "no findings"** |

Collapsing the third into the second is the failure this table exists to prevent:
"it reported nothing" and "we never found out" are not the same statement, and a
round in which one channel failed is not a round that found nothing.

## 4. Decide, per finding

Every finding gets exactly one disposition, with the evidence behind it.

- **Real and in scope** → fix it, **commit it**, then return to step 2 with that
  commit as the new HEAD. The gate runs again on that HEAD and both review
  channels run again against the same HEAD and the recorded base; a fix is not
  done until both have. Gating an uncommitted fix produces evidence for a tree
  the reviewer will then refuse to look at, because step 3 requires a clean
  tree — the loop cannot close any other way.
- **Not real** → refute it with evidence: a test that passes, a line of code that
  already handles it, a scenario that cannot occur. Changing code you believe is
  correct, to make a reviewer stop complaining, is the failure this step exists
  to prevent.
- **Real but out of scope** → do **not** fix it. Record it, stop, and ask the
  founder for authorisation. Never widen the scope quietly.

**What blocks delivery.** A finding of critical or high severity, or one whose
title begins with the out-of-scope marker, blocks until it is fixed or refuted —
**no matter which channel raised it**. An `approve` on one channel does not
release the other channel's findings.

If you match the out-of-scope marker mechanically, anchor it to the **start** of
the title. Matching it anywhere flags findings that merely discuss out-of-scope
work, and a false positive is expensive in both directions.

If both channels report the same defect, say so — it is one defect with two
pieces of evidence, not two defects.

### The loop, in one line

`implement → commit → gate → review both channels → disposition every finding →
(if anything was fixed) commit → gate → review again`. Every gate log and both
channel outputs in a round belong to **one** HEAD. If they do not, the round is
not evidence about anything.

### Run the loop to its end — do not return each round for approval

Inside a work package the founder has already approved, in-scope fixes, their
tests and the re-review **proceed continuously**. Fixing a clear in-scope defect
needs no fresh permission; that permission is what approving the package was.
Stop only at the end of the package, or when a stop condition below fires.

Reporting after every round, or asking whether to fix an in-scope defect the
reviewer just found, is not caution — it spends the founder's attention on
decisions they already made, which is the cost this whole procedure exists to
reduce.

This does **not** loosen the gate tiers. Those decide whether the *package* may
be worked on at all and are settled before implementation starts; the light
gate's STOP ends the turn at the package boundary, not between the rounds of a
package already approved.

## 5. Stop

Stopping is a first-class outcome, not a failure. Stop and report when:

- requirements conflict with each other or with a repository rule
- the correct change needs scope the package was not granted
- a permission or credential would be required
- the round budget or the time budget is exhausted
- the account's quota is exhausted
- the reviewer cannot be reached, or returns nothing usable twice in a row

Report what is outstanding. **Do not start a fresh run to buy back rounds** — the
budget is the budget, and restarting to reset it is the thing the budget exists
to prevent.

## 6. Deliver

Open a **Draft** PR containing:

- what changed and why
- the verification evidence, verbatim, with CI and local reported separately
- every review round with its verdict
- each refuted finding with its refutation
- the open items, including anything awaiting the founder

**Merging is the founder's decision.** This skill never merges and never deploys.

---

## The reviewer itself

Plugin `openai-codex/codex` **1.0.6** at
`~/.claude/plugins/cache/openai-codex/codex/1.0.6/scripts/codex-companion.mjs`,
driven by Codex CLI `0.153.4`. Credentials live outside the repository in
`~/.codex/auth.json`, are never printed, and are never switched to a paid API
plan. If they expire the procedure cannot repair them — stop and say so.

**CI cannot run a real review.** The plugin needs credentials CI does not have.
A review is local evidence attached to the PR, not a CI check.

**The reviewer's approval is evidence, not proof.** It is one more input to a
decision the founder makes.

Known reviewer failure modes, seen in practice:

- A turn that ends without a final message leaves the plugin parsing whatever the
  last message was, producing a JSON parse error. Exit 1 with `parseError` means
  the turn did not complete — not that the model "only planned".
- An account usage limit surfaces its text in `parseError` only when it arrives
  before any assistant message. Arriving later it is lost, and the envelope shows
  a plain syntax error instead. If a call fails with no cause in the envelope,
  the cause may be in the Codex CLI's own trace store rather than in the output.

## Samples

`samples/` holds one isolated reviewer output per condition this procedure has
to handle, with the expected handling stated next to it. They are read by hand;
nothing executes them. A procedure that has never been walked against a planted
flaw is decoration.
