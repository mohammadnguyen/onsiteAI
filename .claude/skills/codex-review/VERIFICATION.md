# Verification of the slim procedure

Founder authorisation of 2026-09-17: verify the procedure before using it — walk
the isolated samples, make **at most one** real read-only Codex call, keep the
raw result, stop and report if the call fails, and start no business work
package.

## 1. Isolated samples — walked, all four as documented

Applied the rules in `SKILL.md` to each sample in `samples/`. Nothing was
invoked; the samples were parsed and judged exactly as the procedure says to.

| Sample | Parsed as | Procedure's answer | Matched |
|---|---|---|---|
| `01-adversarial-findings.json` | dict, `codex.status 0`, `result` present, verdict `needs-attention` | high → blocks; low → reported, does not block; `OUT-OF-SCOPE`-prefixed medium → **blocks at medium severity** and is escalated, never fixed | yes |
| `02-native-envelope.json` | dict, `codex.status 0`, no `result` key (by design on this channel), 705 chars of prose in `codex.stdout` | must be read by hand; its `[P2]` defect is accounted for separately and is not released by the other channel's verdict | yes |
| `03-failure-not-an-envelope.txt` | `json.loads` **refused**: `Expecting value: line 1 column 1` | no verdict is taken from it — note that this sample ends with a literal `Verdict: approve` line, and the procedure never sees it | yes |
| `04-failure-turn-incomplete.json` | dict, `codex.status 1`, `result` null, `parseError` a JSON syntax error over a planning sentence | turn did not complete; not a pass; recorded verbatim; the preamble is not a review | yes |
| `05-budget-exhausted.md` | not an output — a state | stop, deliver Draft marked as not passing review, name the open finding, do not restart to buy back rounds | n/a, read |

Sample 03 is the one that matters most: it is prose containing an approval line,
and the parse-or-refuse rule discards it without ever reading that line. That is
the failure the rule exists for.

**One sample was wrong when first written** and was corrected before this record
was made: the native channel's output was saved as bare prose in a `.txt`. The
native channel really returns a JSON envelope with the prose inside
`codex.stdout`, so the original sample would have taught the procedure to refuse
a valid native result. It is now `02-native-envelope.json`.

## 2. One real Codex call — succeeded

The procedure reviewing itself. Raw result kept in `verification/call-01.txt`,
input verbatim in `verification/call-01.focus.txt`.

```
node .../codex-companion.mjs adversarial-review --wait --json \
  --base 78b5e2be1d47f728d1bb078165ee00a8f654d717 --scope branch -- <focus, 3031 bytes>
cwd:  .../worktrees/codex-direct
base: 78b5e2be1d47f728d1bb078165ee00a8f654d717   (origin/main)
head: 4254402cbbf9469d808636c6d78418596894c832
worktree_clean: yes
exit: 0   duration_seconds: 71.7
```

**Result returned directly to Claude**: `codex.status 0`, `parseError null`, a
structured `result` with a verdict and three findings — no intermediary, no
state file, no controller.

**Bound to the correct code version**: the envelope's own target says so.

```json
{"mode": "branch",
 "label": "branch diff against 78b5e2be1d47f728d1bb078165ee00a8f654d717",
 "baseRef": "78b5e2be1d47f728d1bb078165ee00a8f654d717",
 "explicit": true}
```

`baseRef` is the base that was passed, `explicit: true`, and the tree was clean
at `4254402`, so the reviewed range is exactly `78b5e2b..4254402` — the commit
that introduced this procedure and nothing else.

### What it found — verdict `needs-attention`

> Do not ship yet: the review range can drift, the fix loop skips committing, and
> the required workflow ADR is absent.

| Finding | Disposition |
|---|---|
| **[high]** `SKILL.md` recomputed the base with `git merge-base` inside the review step, contradicting the recorded base. If `origin/main` absorbs part of the package between rounds the new merge base excludes those commits, so later rounds review a smaller range while the report claims the recorded one. | **Fixed.** Step 3 now uses the recorded SHA, says explicitly not to recompute it, and adds `git merge-base --is-ancestor "$BASE" HEAD` with a stop if it fails. |
| **[medium]** The fix loop said "fix it, then return to step 2". Followed literally that gates an uncommitted fix, and the clean-tree assertion then blocks the next review — the advertised loop could not close. | **Fixed.** Step 4 requires committing each fix and carrying that HEAD into both the gate and both channels, plus a one-line statement of the whole loop so it cannot be read out of order. |
| **[medium, OUT-OF-SCOPE]** CLAUDE.md requires an ADR for review-workflow and AI-orchestration decisions. This change introduces a review workflow with no ADR; ADR-001 covers automation generally but does not record this decision. | **Not fixed — escalated.** Out-of-scope findings are never fixed by the run that finds them. Writing the ADR needs founder authorisation. |

The two fixes are **unreviewed**: one call was authorised and it was spent on the
version that preceded them. They are markdown corrections inside the approved
scope, verified by reading, not by a reviewer.

## 3. What this verifies, and what it does not

**Verified.** The plugin can be driven directly, with no controller, and returns
a schema-conforming structured result straight to Claude. An explicit base binds
the review to a named commit range, and the envelope proves which one. The
procedure's parse-or-refuse rule discards a prose approval. The procedure's
handling is written down for a found defect, a failed review and an exhausted
budget. And the procedure caught real defects in itself on its first use.

**Not verified.** That a future session will follow it — nothing here enforces
anything, and a session that does not read the recorded budget will not know it
was spent. That the fix-and-review loop closes in practice: it has never been run
end to end on a real work package. That the gate commands in step 2 are complete
for every change class — they were read from CLAUDE.md and `backend-ci.yml`, not
executed here, because this change is documentation only and has no executable
surface for a gate to exercise.
