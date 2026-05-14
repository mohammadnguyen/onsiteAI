# Response packet pattern

Output-reporting pattern. Every Claude response in this repository ends
with a single fenced markdown block called REVIEW_PACKET that the user
can paste directly into another tool for technical review without
needing repository, terminal, screenshot, or prior-message access.

This is NOT about expense review-queue logic — for that, see
`review-workflow-pattern.md`. This pattern controls Claude's response
shape only.

## Purpose

Make every Claude response machine-readable and copy-paste-friendly
for downstream review (e.g. paste into GPT for a second-opinion read)
without requiring the reviewer to ask follow-up questions about repo
state, what was tested, or what risks were skipped.

## When To Use

Every response. No exceptions. The only variation is depth:

- Implementation / build / database / multi-step work → use the full
  15-section format with full sentences per section.
- Small no-code answers (clarifications, planning-only replies,
  read-only audits, single-question answers) → use the same 15
  headings but write concise one-line entries per section.
- Conversational acknowledgements (e.g. "ok") → still required, kept
  to one line per section where the section is genuinely empty.

If the work is planning-only, write "Planning only — no
implementation." in section 4. If the work is read-only, write
"Read-only audit — no code/data changes." If no code changed, write
"No code changed."

## Standard Structure

Exactly one fenced markdown block at the END of the response,
labelled REVIEW_PACKET, containing the 15 sections below in order.
Section numbering and headings are fixed — do not rename, reorder, or
collapse them.

1. TASK RECEIVED
   - Brief restatement of the requested task.

2. PROJECT CONTEXT
   - Current module/feature affected.
   - Relevant workflow affected.
   - Phase/scope assumptions.

3. STARTING STATE
   - Repo/project, branch, HEAD before work, working-tree status
     before work, existing behaviour, relevant files inspected,
     important assumptions.

4. WORK COMPLETED
   - List each change made. If none: "No code changed." /
     "Planning only — no implementation." / "Read-only audit — no
     code/data changes."

5. FILES CHANGED
   - Per file: Path, Change summary, Reason, Risk level
     (Low / Medium / High). If none: "None."

6. IMPLEMENTATION DETAILS
   - Actual logic changed, key functions/routes/components touched,
     parser implications, review-queue implications, persistence
     implications, mobile UX implications, export implications,
     architectural tradeoffs.

7. COMMANDS / TOOLS USED
   - Per command/tool: name, purpose, result, important output or
     errors.

8. TESTING
   - Tests run, results, manual testing performed, untested areas,
     why untested.

9. ERRORS / BLOCKERS
   - Exact error text if applicable, current blocker status,
     temporary workarounds.

10. RISKS / REVIEW FOCUS
    - Possible bugs, edge cases, tenant-isolation concerns,
      auditability concerns, mobile UX concerns, parser/review
      correctness concerns, export correctness concerns, technical
      debt introduced, areas the downstream reviewer should focus on.

11. PRODUCT BEHAVIOUR AFTER CHANGE
    - Current user-visible behaviour, operational impact, changed
      workflows, anything intentionally unsupported.

12. PHASE / SCOPE COMPLIANCE
    - Confirm whether the work stayed within approved scope, avoided
      speculative features, avoided overengineering, avoided hidden
      AI behaviour, preserved deterministic review behaviour,
      preserved tenant isolation, preserved auditability, avoided
      live DB mutation unless explicitly approved.

13. CURRENT STATE
    - HEAD after work, working-tree status after work, running
      services, test DB state, live DB state, untracked files,
      anything left running or needing cleanup.

14. NEXT RECOMMENDED STEP
    - Single recommended next action. State whether approval is
      required. Do not start it without explicit approval.

15. COPYABLE GPT REVIEW SUMMARY
    - One concise paragraph summarising what changed, what was
      tested, major risks, operational concerns, recommended next
      step.

## Rules

- Exactly one fenced markdown block per response. The REVIEW_PACKET
  is that block.
- The REVIEW_PACKET MUST be the last thing in the response. Nothing
  may appear after the closing fence.
- No other fenced markdown blocks anywhere in the response. Code
  snippets, commands, diffs, logs, and examples must be either
  described in plain prose outside the packet or included as plain
  text inside the packet.
- Do not split the packet across multiple fenced blocks.
- Do not omit blockers. If there is a blocker, name it in section 9
  AND in section 15.
- Do not bury uncertainty. State assumptions in section 3 and risks
  in section 10.
- Do not claim something was tested if it was not actually tested.
  Section 8 must distinguish "tests run" from "untested areas + why".
- If the working tree is claimed clean, verify with
  `git status --porcelain` in the same turn.
- If the live DB is claimed unchanged, verify it in the same turn or
  write "Live DB not touched / not freshly verified."
- If a response is planning-only, say so in section 4.
- If a response is a read-only audit, say so in section 4.
- If implementation quality is uncertain, say so in section 10 or 15.

## Anti-Patterns

- Multiple fenced blocks in one response (e.g. one for code + one for
  the packet). Anything not the REVIEW_PACKET must be plain text.
- Code snippets in their own fenced blocks. Embed inside the packet
  as plain text or describe in prose.
- Splitting a single REVIEW_PACKET across two fenced blocks.
- Trailing text or commentary after the closing packet fence.
- Empty placeholder sections like "section 8: TBD" or "see above".
  If a section is genuinely empty, write a single one-liner that
  says so explicitly (e.g. "None.").
- Claiming "tests pass" or "build clean" without listing the
  command, the result, and the timestamp/turn it was actually run.
- Claiming "working tree clean" when `git status --porcelain` was
  not run in the same turn.
- Claiming "live DB unchanged" when no read-only verification was
  performed in the same turn.
- Repeating the entire packet in section 15. The summary is one
  paragraph, not a re-quote.

## Testing Expectations

- Conformance is human-verified per response. There is no automated
  check (and adding one would itself need its own ADR + plan).
- The downstream reviewer (GPT or human) is expected to spot-check
  that the packet is well-formed, sections are filled honestly, and
  blockers/risks/uncertainty are not buried.
- Authoring rule: see CLAUDE.md "Response Packet Rule" for the
  always-loaded contract. This file is the long-form template.
