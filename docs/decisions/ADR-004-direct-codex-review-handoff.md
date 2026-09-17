# ADR-004: Direct Codex review handoff

**Status:** Accepted — in use for approved work packages, as a procedure.
**Date:** 2026-09-17
**Supersedes in practice:** the controller proposed in ADR-003, which is paused.

> Numbering: `003` is claimed on the paused `claude/dev-review-handoff` branch by
> a different ADR that has never merged. This one takes `004` so two different
> ADR-003s never exist. If that branch is ever revived its ADR is renumbered.

## Context

Development review was to be automated by a controller program
(`scripts/review_handoff/`, ADR-003): a deterministic runner holding run state,
round counters, locks, tree digests and a linked-run history, driving the
installed Codex plugin on the agent's behalf.

It was built and never accepted. Across seven runs it carried **no** approving
review verdict, and every review round found real defects in the controller
itself rather than in the work it was meant to be reviewing. Two defects remain
open at its frozen head `21cd6d2`: inherited out-of-scope findings have no
authorised-recovery path, and `run_id` is a directory basename that two runs can
collide on. Two of its review rounds also failed plugin-side — one traced to an
account usage limit, one with no established cause.

ADR-001 §3 already says a capability defaults to the **lowest layer that can do
the job**, and that moving up requires demonstrated context isolation,
permission isolation, resource specialisation or genuine parallelism. The
controller demonstrated none of those. It was a layer that existed to hold
bookkeeping about itself.

## Decision

**Claude calls the installed Codex plugin directly.** The procedure lives in
`.claude/skills/codex-review/` as documentation: a skill, a review-input
template, isolated samples, and the verification record. There is no controller,
no run state, no scheduler, no history inheritance and no authorisation
mechanism.

The review substance that the controller had learned the hard way is carried
over as written rules rather than as code: pin the review base and reuse it;
commit before reviewing, because uncommitted work is invisible to the reviewer;
run the gate sequentially; never review on a failing gate; call both channels;
parse before reading, and validate before trusting; account for every channel in
writing; refute with evidence rather than appeasing; escalate out-of-scope work
instead of fixing it; and treat a blocking finding on either channel as blocking
regardless of the other channel's verdict.

### Scope

Applies to **work packages the founder has already approved**. Within one
approved package Claude runs the gate, calls the reviewer, fixes in-scope
defects, re-tests and re-reviews continuously, and reports once at the end of
the package or when a stop condition fires. It does not merge, does not deploy
and does not start a pilot.

It does not apply to unapproved work, and it does not grant scope: work outside
an approved package's allowed paths is escalated, never done.

### Limits, stated as limits

The round budget, the time budget and the stop conditions in this procedure are
**a procedure Claude follows and reports against. They are not enforced by any
mechanism.** Nothing counts rounds, nothing survives a session ending, and
nothing guarantees unattended operation across sessions. The only defence is
that the budget and the rounds used are written into the PR body at every round,
where the next session and the founder can both read them. Any claim of
enforcement would be false and this ADR exists partly to prevent one being made.

## Consequences

**Good.** The layer that produced the defects is gone. Review is one command
whose raw output is read directly, so a failure is visible at the point it
happens rather than mediated by a program that had to be right about it. The
procedure is version-controlled, reviewable as text, and changed by editing a
document. ADR-001's measure — founder attention per verified change — improves,
because there is no second system to review.

**Bad, and accepted.** What a program enforced, a person now has to remember.
There is no mechanical guarantee that a budget is respected, that both channels
are read, or that a stopped package is not quietly restarted. Determinism is
traded for the thing that was actually failing, which was the determinism
machinery itself.

**PR #15 is paused, not abandoned.** Its branch, code, tests, run records and
review archives are retained unchanged. Nothing from it is merged. If the
manual procedure proves insufficient, the argument for mechanising it starts
from evidence collected under this ADR rather than from the assumption that a
controller is needed.

**Still unverified at the time of writing.** The fix-and-review loop has not run
end to end on a real work package. The plumbing is verified — a direct call
returns a schema-conforming structured result bound to a named commit range —
and the procedure caught real defects in itself on first use, but neither of
those is the same as a completed business work package.
