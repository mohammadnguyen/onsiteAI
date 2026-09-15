# ADR-003 — Development/review handoff

**Date:** September 2026
**Scope:** How one founder-approved work package runs from approval to Draft
PR, and what the primary agent may decide on its own while it does. Product
behaviour is untouched.

## Record of standing

Three different things were previously collapsed into one word, "Accepted".
They are separate, they are granted by separate acts, and none of them
implies the others:

| Record | State | Evidence |
|---|---|---|
| **Authorisation** to build this capability | **granted** | the founder's instruction opening the work package, and the supplementary correction instruction that followed |
| **Acceptance** of the delivered implementation | **not given** | the change is a Draft PR; no acceptance instruction exists |
| **Trial approval** (permission to actually use it on a package) | **not given** | no trial has been authorised or started |
| **Kill criterion** (below) | **PROPOSED** | unsigned; only the founder signs it |

Nothing here may be read as the founder having approved the design, the
sceptic verdict having been adjudicated, or a trial being under way.

## Context

Work packages here already follow a loop: implement, run the repository's
gates, hand the diff to an external reviewer (the installed Codex plugin),
fix what it finds, review again. The loop works — the most recent package
found real defects across several rounds — but it was never written down.
Each package's rules lived in a hand-written instruction: the round cap was
a sentence in that instruction, the review base was re-bound to the previous
round's head, and "the review completed" had no definition at all.

Rounds inside an authorised package already ran without asking the founder
between them; what was missing was not the relay but the enforcement. The
cap, the base and the completion test were prose, so they held only for as
long as someone remembered them, and nothing carried across a restarted
session. That, plus ADR-001 §8 — the development system is measured by
founder attention per verified change — is the whole motivation.

## Decision

### 1. The capability stays at L1 + L2

A deterministic script package (`scripts/review_handoff/`) owns everything
mechanical: brief validation, base pinning, running the gates, invoking the
reviewer with a timeout, archiving raw output, reading the verdict and the
findings, counting rounds, and the time budget. A versioned skill
(`.claude/skills/dev-review-handoff/`) owns the judgement the primary agent
was already doing. No subagent, no service, no new credential, no new
runtime. ADR-001 §3 requires the mechanical half to sit at L1, and this is
that move.

### 2. ADR-001 §5 is amended

§5 read "Only two true skills exist at present: plan-review and
sceptic-review." That count becomes three. The rest of §5 stands and is
honoured here: `fixtures/` holds one reviewer response per handled condition
with the action it must produce, and the tests assert those actions. Both
older skills remain without fixtures; that gap is recorded, not fixed by
this ADR.

### 3. What the agent may decide alone, and what it may not

May: whether a reported defect is real, how to fix it, and whether to refute
it with evidence. Must not: widen the approved scope, touch a credential or
permission, merge, deploy, continue past a limit, or stop another session or
shared runtime. On any of those it stops and reports, which is a first-class
outcome with its own command.

### 4. A verdict binds to inputs

Each review records the base, the head and a digest of the working tree. An
approval that describes a different tree is reported as stale and cannot
close a run. The base is pinned once, at the merge base with the integration
branch, resolved to a full commit SHA and never re-bound — re-basing each
round onto the previous head is how a package reaches the end without
anything having reviewed it whole, and a base stored as a *name* re-points
silently the moment anything is committed.

The run's own artefacts are excluded from that digest. A run directory
inside the repository would otherwise become part of the fingerprint of the
code it measures.

### 5. Everything ambiguous fails closed

No verdict, two conflicting verdicts, a crash, a timeout, an empty result, a
channel that did not complete, a structured finding that cannot be read, or
a failing gate: all are "unusable", and unusable is never a pass (ADR-001 §7
— uncertainty is surfaced, never folded into pass).

### 6. Structured results are read; prose is never classified

The plugin runs its adversarial channel against its own JSON output schema,
so that channel returns an object: a verdict and a list of findings with
severities. That object is **read** — mechanically, with no interpretation —
and it is preferred over any verdict line in the surrounding prose.

The other channel returns prose. It is run and archived because it has
caught defects the first channel missed, but its content is never
pattern-matched into a score. The agent reads it and records what it found;
that record is an explicit act, not an inference. No model judges another
model here.

### 7. A verdict is not a release condition

Every finding is recorded individually with a disposition: `pending`,
`fixed`, `refuted`, or `awaiting-adjudication`. A run may not be delivered
while any *blocking* finding (critical or high, or anything the reviewer
marked out-of-scope) is unresolved — **whichever channel raised it**. One
channel's `approve` does not release the other channel's findings; the two
channels disagree in practice, and letting either clear the other would
systematically lose exactly the findings only one of them ever sees.

A channel that produced a review must also end up triaged: findings
recorded, or an explicit attestation that it reported none. Silence is not
evidence that nobody looked.

### 8. The reviewer is told the project's standard, and told not to conceal

The instruction text is part of the workflow's behaviour. It names the
standard this repository judges by — correctness, maintainability, project
fit — and explicitly says not to prefer a smaller change to a correct one.
An earlier version asked for "a minimal in-scope fix" and told the reviewer
not to propose work outside the allowed scope; both selected for the wrong
answer, and the second asked a reviewer to keep quiet.

Necessary work outside the approved scope is now reported, marked, recorded
as awaiting adjudication, and **not implemented**. The run stops and asks
the founder.

### 9. Concurrency is a correctness property, not a nicety

Two commands on one run take the run's lock; a second is refused and told
who holds it. Runs in different worktrees still share one PostgreSQL
instance, so verification takes a machine-wide lock named by the brief and
queues behind it — concurrent suites there have already produced phantom
failures in this repository. A held lock is never forced and no holder
process is ever signalled; breaking a lock left by a crashed session is an
explicit flag and is recorded in the run.

A command's own subprocess tree is terminated on timeout, so a killed gate
leaves no worker still writing to that shared database. Only processes the
run itself started are touched.

## Rejected alternatives

**Pure L1, founder reads every round.** The strongest alternative, and most
of it is adopted: the script package exists either way. Kept as the fallback
if the trial fails.

**A checklist in CLAUDE.md.** Near-zero cost, but prose rules are exactly
what failed to be enforced, and a checklist cannot count rounds across a
restart or hold a time budget.

**A reviewer-scoring layer** that reads findings and decides severity.
Rejected: it is a second model judging a first model, with no ground truth
and no way to test it. §7 deliberately records dispositions rather than
computing them.

## Sceptic review (ADR-001 §6)

Run in a clean-context subagent during implementation, after the work
package was authorised. Verdict: **insufficient-evidence**, on the grounds
that no documented failure of the current layer exists — the loop has never
produced a wrong outcome, and its most consequential act on record was
stopping to escalate a finding that needed authorisation nobody had. It also
found the proposal's claim that "no round cap exists" to be false: the cap
existed in the founder's instruction and held; what was missing was
versioning.

Its substantive findings were adopted into this design: run both review
channels rather than one, pin the base to the merge base, keep the run state
inside the worktree, fail closed on an unparseable verdict, and record the
run's own cost so the metric in ADR-001 §8 becomes measurable.

**The founder had not seen this verdict when the work package was
authorised**, and has not adjudicated it since. The disagreement stands
unresolved and is recorded here rather than closed.

## Correction round — what the first self-run does and does not prove

The workflow was run on itself (run `self-v2`, four rounds, stopped at its
round limit). Those rounds found and fixed twelve real defects and are kept
as history, with their raw output archived.

They are **not** evidence that the reviewer read the requirements and the
verification logs. In all four rounds the instruction text was passed to the
plugin as a single argument, and the plugin re-splits a single raw argument
with a shell-like tokeniser: the backslashes were consumed out of the
Windows paths pointing at the archived gate logs, so those paths did not
exist, and apostrophes in the approved requirements were altered. What the
reviewer received was corrupted. Its findings were still real — it read the
diff, which it fetches itself — but any claim that it read the brief and the
evidence is unsupported for those rounds.

The argument passing is now one argv element per flag with the prose after a
`--` terminator, checked against the installed plugin's own parser, and a
real handoff was run after the fix. `self-v2` keeps its counts, its history
and its stopped status; the corrected work is a new run linked to it.

## Kill criterion (PROPOSED — only the founder signs)

Trial over the next three work packages. The capability is removed and the
loop returns to founder-relayed rounds if any of:

1. one escaped defect reaches a merged PR that a round the founder did not
   read had reported, and the agent refuted or classified as not-real;
2. more than 40% of automatic fix rounds across the trial change only test
   or scaffolding files while both review channels report the production
   code clean — the loop converging on itself rather than on the work;
3. founder minutes per verified change is not lower than the baseline. The
   baseline does not exist yet and must be recorded before the trial starts,
   or this criterion is unmeasurable and the trial should not begin;
4. any package where the recorded review base is not the merge base, or
   where the archived artefact count disagrees with rounds × channels.

Review at three packages. Extension requires a signature, not silence.

## Lineage

`check_decision_drift.py` plus `tests/test_check_decision_drift.py` is the
shape this follows: a deterministic gate with its own fixture suite, wired
into the gate sequence, where the fixtures are the proof it does anything.
