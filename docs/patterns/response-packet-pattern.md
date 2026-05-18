# Response packet pattern

PACKET_SCHEMA_VERSION: 1.0
RULESET_VERSION: 2026-05-18-r22

Output-reporting pattern. Every Claude response in this repository ends
with a single fenced markdown block called REVIEW_PACKET that the user
can paste into another tool for technical review without needing
repository, terminal, screenshot, or prior-message access.

This file is the canonical source for packet format. Future packets
reference it by version (`PACKET_SCHEMA_VERSION` + `RULESET_VERSION`
above); changes to the format flow through this file, not through
inline conversation.

## Purpose

Make every Claude response machine-readable and copy-paste-friendly for
downstream review (paste into a peer model or a human reviewer) without
requiring follow-up questions about repo state, what was tested, or
what risks were skipped.

## When to use

Every response. Variation is in compression depth, not presence:

- Implementation / build / DB / deploy work → full sections.
- Small no-code answers (clarifications, planning-only replies,
  read-only audits) → same 15 headings, concise one-line entries.
- Conversational acknowledgements ("ok", "approved") → still required;
  omit-able sections marked `N/A` in one line.

Sections scale with operational significance. Material risks, state
changes, DB writes, service changes, commits, migrations, and rollback
implications are never compressed away.

## Section structure

One fenced markdown block at the END of the response, labelled
REVIEW_PACKET. Two header lines (`PACKET_SCHEMA_VERSION` +
`RULESET_VERSION`) appear immediately after the fence open. Then the 15
numbered sections, in order, headings unchanged:

1. TASK RECEIVED
2. PROJECT CONTEXT
3. STARTING STATE — task-relevant inputs only
4. WORK COMPLETED
5. FILES CHANGED
6. IMPLEMENTATION DETAILS
7. COMMANDS / TOOLS USED
8. TESTING
9. ERRORS / BLOCKERS
10. RISKS / REVIEW FOCUS
11. PRODUCT BEHAVIOUR AFTER CHANGE
12. PHASE / SCOPE COMPLIANCE
13. CURRENT STATE — single authoritative runtime snapshot
14. NEXT RECOMMENDED STEP
15. COPYABLE GPT REVIEW SUMMARY

Optional sub-sections (e.g. `12a INVARIANT TRACKING`) attach to their
parent number with a letter suffix.

## The 22 consolidated rules

Numbered for stable cross-reference from future packets. Some rules are
listed together (`a/b`) where a later rule refines an earlier one;
treat each pair as one coherent rule.

### State & verification

1. **Verification state.** Every state claim labelled
   `VERIFIED_THIS_TURN`, `CARRIED_FORWARD_NOT_REVERIFIED`, `ASSUMED`,
   or `PLANNED_ONLY`. Vague words ("carried", "still", "unchanged")
   appear only paired with one of those labels.

9. **State snapshot split.** §3 = task-relevant inputs only. §13 =
   single authoritative runtime snapshot. Do not duplicate §3 content
   into §13 unless the state changed mid-turn.

### Decisions, plans, uncertainty

2/11/20. **Decision and plan classification.** Architecture /
operational statements tagged `FACT`, `ASSUMPTION`, `RECOMMENDATION`,
`DECISION_ALREADY_APPROVED`, or `OPEN_DECISION`. Open items use
exactly one of `OPEN_DECISION` (explicit approval required),
`OPEN_QUESTION` (clarification useful but not blocking), or
`OBSERVATION` (informational only). Report `PLAN_STATUS` and
`EXECUTION_STATUS` as two separate fields, never combined.

10. **Uncertainty classification.** Estimates and operational judgements
    tagged `HIGH_CONFIDENCE`, `MEDIUM_CONFIDENCE`, or `LOW_CONFIDENCE`.
    Apply to: migration safety, rollback safety, LOC/time estimates,
    infra reliability assumptions.

### Tools, scope, autonomy

3. **Tool usage enumeration.** When no operational actions occurred,
   explicitly enumerate: no filesystem reads / writes / shell / git /
   DB / network / provider. Never summarise as "reasoning only".

14. **Internal mechanics hidden.** No references to model-control
    primitives or tool-routing detail. Report externally meaningful
    actions only.

5. **Scope expansion declaration.** Explicitly declare new
   infrastructure / operational / deployment / CI-CD / monitoring /
   secret-management requirements introduced. If none, state "No
   hidden scope expansion introduced."

13/19. **Autonomy boundary.** Every future step labelled
    `SAFE_FOR_AUTONOMOUS_EXECUTION` OR `HUMAN_OPERATOR_REQUIRED`, plus
    one of `REQUIRES_INFRA_ACCESS` / `REQUIRES_PRODUCTION_ACCESS` /
    `LOCAL_ONLY_OPERATION`. `SAFE_FOR_AUTONOMOUS_EXECUTION` requires
    ALL eight conditions: fully reversible via git; no external /
    provider side effects; no secrets exposure; no live DB writes; no
    irreversible migrations; no production traffic impact; no infra
    provisioning; no remote deployment actions.

4. **Rollback classification.** Every infra / config / deployment
   change labelled `Reversible via git only`, `Reversible via manual
   operator action`, `Requires provider action`, or `Potentially
   irreversible`.

### Risks, invariants, change impact

7/12. **Risk format.** Each meaningful risk = Category + Likelihood +
    Impact + Trigger + Owner + Mitigation. Categories: Product /
    Operational / Infrastructure / Data-integrity / Developer-workflow.
    Workflow inconveniences (e.g. "packets get longer") are not risks.

18. **Invariant tracking.** Turns touching system behaviour classify
    SiteTracker invariants as `PRESERVED` / `MODIFIED` /
    `NOT_EVALUATED`. "Not touched" ≠ "verified preserved". Canonical
    invariants: tenant isolation, audit append-only behaviour,
    deterministic review routing, no silent auto-assignment, immutable
    expense history, snapshot-consistent exports, bilingual command
    compatibility.

15. **Change impact classification.** Implementation turns explicitly
    classify: Runtime / Schema / Operational workflow / Mobile UX /
    Deployment / Rollback complexity. Separate from risks.

### Acceptance, planning, governance

6. **Acceptance criteria enumeration.** Do not summarise ("matches
   user list", "verbatim"). Enumerate criteria literally or reference
   numbered source items.

8/16. **Planning-only & planning purity.** Planning turns state "no
    runtime change", "no deployable artefact produced", and whether
    future steps require human operator execution. Planning language
    strictly distinguishes `proposed` / `approved` / `implemented` /
    `verified`. Do not imply infra/resources were provisioned.

17. **Schema version headers.** Every packet declares
    `PACKET_SCHEMA_VERSION` and `RULESET_VERSION` immediately after the
    fence open. Format: `PACKET_SCHEMA_VERSION: <semver>` and
    `RULESET_VERSION: <YYYY-MM-DD>-r<count>`.

22. **Governance freeze.** No inline rule additions in execution
    packets. Further governance changes flow through this pattern doc,
    an ADR, or a schema version bump. Refuse inline rule adoption;
    surface as `OPEN_DECISION` for a separate doc batch.

## Section compression guidance

Doctrine: sections scale with operational significance.

Bias toward fuller packets for: live DB writes, code commits, schema
migrations, service starts/stops, new infrastructure, deploys (any
environment), rollbacks, security-relevant changes.

Bias toward compression for: meta acknowledgements, doc-only typo
fixes, trivial copy edits, hold/wait turns, planning-only audits
without inspection.

Never compress away (regardless of perceived significance): material
risks, state changes (HEAD, working tree, DB, services), commits,
migrations, rollback implications, secrets exposure, live-data impact.

## Required state checklist per packet

Every packet, regardless of compression, must report or label:

1. HEAD on each worktree (verified or carried-forward).
2. Working tree state (clean or list non-trivial entries).
3. DB touch (none / read-only / write — with rollback reference).
4. Service state (which started / stopped / unchanged).
5. Files changed (committed-bound + ignored local edits).
6. Tests run (yes/no, command, result).
7. Live-data impact (none / read-only / write with audit reference).
8. Next approval gate (the specific user instruction expected).

Omitting any of these in a packet that touched corresponding state is
an audit failure.

## Examples

### Implementation packet (full sections)

Use for: feature batches, refactors, deploy turns, schema migrations.
Distinctive sections:
- §5 enumerates every file with change summary + risk level.
- §10 uses the full risk table (Category + Likelihood + Impact +
  Trigger + Owner + Mitigation).
- §12a `INVARIANT TRACKING` explicitly evaluates each touched
  invariant.
- §15 includes a `CHANGE IMPACT` block.

### Git-only packet (fast-forward / rebase / branch ops)

Use for: pure git pointer moves, fast-forwards, branch deletions.
Distinctive:
- §6 `IMPLEMENTATION DETAILS` = "N/A — git pointer move only".
- §10 single-row risk table at most.
- Rollback classification: `Reversible via git only`.
- Invariants: `NOT_EVALUATED` (no code / DB change).

### Live DB cleanup packet

Use for: data hygiene, ad-hoc admin actions, anything writing the live
DB outside the normal app flow.
Distinctive:
- Mandatory pre-write backup section.
- §14 sequential approval gates (Gate 0 backup / Gate N action / Gate
  close-out).
- Per-step verification (HTTP code → GET → counts → audit row).
- §12a explicit; audit-append-only must remain `PRESERVED`.
- Live-data impact: write — with rollback reference to backup file
  path.

### Low-significance meta packet

Use for: rule acknowledgements, status confirmations, hold/wait turns.
Distinctive:
- Most sections compressed to one line or `N/A`.
- §7 still enumerates the 7 NOs explicitly.
- §10 risk table omitted or single row.
- §13 mostly `CARRIED_FORWARD_NOT_REVERIFIED`.

## Anti-patterns

- Multiple fenced blocks in one response.
- Code snippets in their own fenced blocks (must be plain text inside
  the packet or in prose outside it).
- Trailing prose after the closing fence.
- Empty placeholder sections ("TBD", "see above"). Use a one-liner
  instead.
- Bloated packets for trivial turns.
- Compressed packets for risky operations (DB writes, deploys, schema
  changes).
- Adding rules inline after the governance freeze (rule 22).
- Saying "done" without state verification.
- Hiding DB / service / file changes by omission.
- Using `OPEN_DECISION` for non-blocking observations.
- Presenting recommendations as facts (violates rule 2).
- Exposing internal orchestration primitives (violates rule 14).
- Claiming "tests pass" or "working tree clean" without running the
  command this turn (violates rule 1's `VERIFIED_THIS_TURN`
  requirement).
- Inflating `SAFE_FOR_AUTONOMOUS_EXECUTION` beyond the eight
  conditions in rule 13/19.

## Relationship to other repository docs

- **CLAUDE.md** is the always-on contract. Its "Response Packet Rule"
  section names this file as the format authority. The rules here do
  not override CLAUDE.md product / safety / architecture rules.
- **docs/adr/** holds architectural decision records. New ADRs trigger
  packets that exercise these rules (especially rules 4 rollback,
  13/19 autonomy, 18 invariants).
- **docs/operations/** holds runbooks and procedural docs
  (env-and-secrets; future staging-deploy; future rollback). Packets
  for operational turns reference these docs as the source of truth
  for the procedure; the packet records the execution + verification,
  not the procedure itself.
- This file is the canonical source for packet format. Future
  governance changes (additions or revisions of rules 1-22) flow
  through a pattern-doc edit (PR / commit visible in git history) and
  a `RULESET_VERSION` bump. Inline rule additions in execution packets
  are forbidden by rule 22.

## Versioning policy

- `PACKET_SCHEMA_VERSION` bumps when the 15-section structure itself
  changes (sections added, removed, renumbered, or renamed).
- `RULESET_VERSION` bumps when the 22 rules change (any rule added,
  removed, materially reworded, or superseder relationship resolved).
  Format: `YYYY-MM-DD-r<count>` (date of bump + total active rule
  count).
- Old packets in git history remain valid under their declared
  versions; do not retroactively edit closed packets.
