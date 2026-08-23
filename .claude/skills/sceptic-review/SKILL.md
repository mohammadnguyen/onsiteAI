---
name: sceptic-review
description: Run whenever anyone (including the primary agent) proposes moving a capability up the automation ladder (deterministic tool → skill → subagent → service), adding a new agent/service, or granting new autonomy. Must run in a clean-context subagent that has NOT seen the proposer's reasoning.
---

# sceptic-review

This is a procedure, not a persona. The reviewer receives **only** the proposal text and the referenced evidence — never the conversation that produced it (context isolation is the point; see ADR-001 §6).

## Invocation

Primary agent packages: (a) the proposal in ≤1 page, (b) links/paths to raw evidence (eval results, failure logs, timings), (c) the current layer of the capability. Spawn a clean-context subagent with this skill and those inputs only.

## The six questions

Answer each with evidence or explicitly write "NO EVIDENCE":

1. **Evidence** — What concrete failures of the *current* layer are documented? (Counts, links. Anecdotes are marked as anecdotes.)
2. **Data dependency** — What data does the proposed layer depend on, and what happens when it is stale, missing, or wrong?
3. **Behaviour dependency** — What founder/user behaviour must change for this to work? Is that change already observed, or hoped for?
4. **Failure consequence** — When (not if) it fails, what breaks, who notices, how is it rolled back?
5. **Simpler alternative** — What is the strongest version of solving this one layer lower? Why is it insufficient? (This question must be answered in good faith, not as a strawman.)
6. **Kill criterion** — Propose a measurable condition under which this capability is removed. Mark it PROPOSED — **only the founder signs kill criteria.**

## Output format

```
SCEPTIC-REVIEW
Proposal: <one sentence>
Current layer: L_ → proposed L_
Q1 Evidence: ...
Q2 Data dependency: ...
Q3 Behaviour dependency: ...
Q4 Failure consequence: ...
Q5 Simpler alternative: ...
Q6 Kill criterion (PROPOSED): ...
Verdict: upgrade-justified | stay-at-current-layer | insufficient-evidence
```

`insufficient-evidence` is the default verdict when Q1 has no documented failures.

This block supplements, not replaces, the repository's Response Packet Rule: emit this block first, then end the response with the standard REVIEW_PACKET.
