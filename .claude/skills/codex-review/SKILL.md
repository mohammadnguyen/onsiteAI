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
BASE=$(git merge-base origin/main HEAD)
PLUGIN=~/.claude/plugins/cache/openai-codex/codex/1.0.6/scripts/codex-companion.mjs

node "$PLUGIN" adversarial-review --wait --json --base "$BASE" --scope branch -- "<review input>"
node "$PLUGIN" review            --wait --json --base "$BASE" --scope branch
```

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

Parse `stdout` as JSON. It must yield an object. That is the whole test — no
first-character check, no hunting for an object embedded in prose, no second mode
for output that merely resembles JSON. Those heuristics are how a non-conforming
answer becomes an approval. **stderr is diagnostic only and never carries a
verdict.**

The adversarial channel returns a structured `result` with a verdict and
findings. The native channel returns prose in `codex.stdout`; **read it
yourself** — nothing classifies it for you.

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

For each channel, either enumerate its findings or state that you read it and it
reported nothing. There is no third state. And keep "it reported nothing"
distinct from "we never found out" — they are not the same statement.

## 4. Decide, per finding

Every finding gets exactly one disposition, with the evidence behind it.

- **Real and in scope** → fix it, then return to step 2. The gate runs again and
  the review runs again; a fix is not done until both have.
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
