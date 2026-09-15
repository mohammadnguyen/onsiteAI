# ADR-003 — Development/review handoff

**Status:** Accepted (v1, trial)
**Date:** September 2026
**Scope:** How one founder-approved work package runs from approval to Draft
PR, and what the primary agent may decide on its own while it does. Product
behaviour is untouched.

## Context

Work packages here already follow a loop: implement, run the repository's
gates, hand the diff to an external reviewer (the installed Codex plugin),
fix what it finds, review again. The loop works — the most recent package
ran nine rounds and the reviewer found real defects in three of them — but
it was never written down. Each round's rules lived in a hand-written brief,
the round cap lived in a sentence in the founder's instruction, the review
base was re-bound to the previous round's head, and every intermediate
verdict was relayed through the founder.

Two consequences. First, the founder is in the loop for opinions that do not
need them, which spends the one resource ADR-001 §8 says the development
system is measured by. Second, nothing was enforced: the cap, the base and
the definition of "the review completed" were prose, so they held only for
as long as someone remembered them.

## Decision

### 1. The capability stays at L1 + L2

A deterministic script package (`scripts/review_handoff/`) owns everything
mechanical: brief validation, base binding, running the gates, invoking the
reviewer with a timeout, archiving raw output, extracting the verdict,
counting rounds, and the time budget. A versioned skill
(`.claude/skills/dev-review-handoff/`) owns the judgement the primary agent
was already doing. No subagent, no service, no new credential, no new
runtime. ADR-001 §3 requires the mechanical half to sit at L1, and this is
that move.

### 2. ADR-001 §5 is amended

§5 reads "Only two true skills exist at present: plan-review and
sceptic-review." That count becomes three. The rest of §5 stands and is
honoured here: `fixtures/` holds one reviewer response per handled
condition with the action it must produce, and the tests assert those
actions. Both older skills remain without fixtures; that gap is recorded,
not fixed by this ADR.

### 3. What the agent may decide alone, and what it may not

May: whether a reported defect is real, how to fix it, and whether to refute
it with evidence. Must not: widen the approved scope, touch a credential or
permission, merge, deploy, or continue past a limit. On any of those it
stops and reports, which is a first-class outcome with its own command.

### 4. A verdict binds to inputs

Each review records the base, the head and a digest of the working tree. An
approval that describes a different tree is reported as stale and cannot
close a run. The base is pinned once, at the merge base with the integration
branch, and never re-bound — re-basing each round onto the previous head is
how a package reaches the end without anything having reviewed it whole.

### 5. Everything ambiguous fails closed

No verdict line, two conflicting verdicts, a crash, a timeout, an empty
result, a channel that did not complete, or a failing gate: all are
"unusable", and unusable is never a pass (ADR-001 §7 — uncertainty is
surfaced, never folded into pass).

### 6. The reviewer's wording is never classified

Only an explicit `Verdict:` line is read. The plugin's other channel emits
prose with no verdict; it is run and archived because it has caught defects
the first channel missed, but its content is read by a human or the agent,
never pattern-matched. A classifier over reviewer prose would put a second
stochastic component where a gate belongs.

## Rejected alternatives

**Pure L1, founder still reads every round.** The strongest alternative, and
most of it is adopted: the script package exists either way. What it does
not do is remove the relay, which is the thing the founder asked for. Kept
as the fallback if the trial fails.

**A checklist in CLAUDE.md.** Near-zero cost, but prose rules are exactly
what failed to be enforced, and a checklist cannot count rounds across a
restart or hold a time budget.

**A reviewer-scoring layer** that reads findings and decides severity.
Rejected: it is a second model judging a first model, with no ground truth
and no way to test it.

## Sceptic review (ADR-001 §6)

Run in a clean-context subagent before implementation. Verdict:
**insufficient-evidence**, on the grounds that no documented failure of the
current layer exists — the loop has never produced a wrong outcome, and its
most consequential act on record was stopping to escalate a finding that
needed authorisation nobody had. It also found the proposal's claim that "no
round cap exists" to be false: the cap existed in the founder's instruction
and held across all nine rounds; what was missing was versioning.

Its substantive findings were adopted into this design: run both review
channels rather than one, pin the base to the merge base, keep the run state
inside the worktree, fail closed on an unparseable verdict, and record the
run's own cost so the metric in ADR-001 §8 becomes measurable.

The founder authorised the work package with that verdict visible. The
disagreement is recorded rather than resolved: this is a **trial**, and the
kill criterion below is the condition under which the trial ends.

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
