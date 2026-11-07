# DEC-PV-001 — sceptic-review record

PRODUCT.md §5 requires a sceptic-review record when a DIRECTION item is promoted into the active slice. This is that record for DEC-PV-001 (Charter Amendment 11): the reviewer's output verbatim, the reviewer's stated limits, the proposer's additions, and the founder's adjudication.

## Review (verbatim, clean-context reviewer, 2026-11-07)

File and line references are to the repository at `c955b1c`, before Amendment 11. `ux-issues.md` is the private operations journal, kept outside the repository.

```
SCEPTIC-REVIEW
Proposal: Add DEC-PV-001 and a human-only, admin-reviewed Potential Variation flag on Site Log entries, validated under DEC-GATE-H3-001.
Current layer: L0 (free text plus memory; below ADR-001:21-24) → proposed L1 (deterministic)
Q1 Evidence: NO EVIDENCE of a documented failure. Friction log has 0 entries on any local ref (FOUNDER_FRICTION_LOG.md:15; git log). ux-issues has 2 rows, neither about scope (ux-issues.md:15-16). Charter:1745 "Founder firsthand evidence" is an anecdote: no case, count or amount.
Q2 Data dependency: Entry, pinned revision, job copy, human judgement. Stale: a Task-style pin is a bare integer, no FK (task.py:154); revision retention is "where technically appropriate" (Charter:146). Job copy ignores re-links (task.py:18-21): a corrected entry leaves the flag on the wrong Job. Missing: NULL job (task.py:141-146) hides a flag from by-job lookup; who sets it is unstated. Unstated: pre-fill for entries without text; whether the read rule gates pinned text (access.py:19-20).
Q3 Behaviour dependency: Someone must recognise a scope change and flag it; an admin must open the list unprompted. Both rely on memory. Founder use on real jobs: NO EVIDENCE; H1's required log is empty (PRODUCT.md:187). Founder says, without specifics, the Site Log workflow "needs to be changed" (ux-issues.md:15). Events may be sparse (Charter:1670). Hoped for, not observed.
Q4 Failure consequence: Likely (inference): a flag sits in To Check, feels handled, nobody notices until a dispute. False pass: "earlier scope check" omits the commercial materiality H3 requires (Charter:116, 1386), and the founder would judge their own counterfactual. Rollback is unstated but cheap if additive like Task (task.py:3): hide routes, keep rows, withdraw the decision by Charter PR (PRODUCT.md:216-218).
Q5 Simpler alternative: No code: from the entry, make an ordinary Task titled "PV: ...", self-assigned. Task already has pre-fill (task.py:11-13), pinned revision (task.py:148-154), job copy, audit (task.py:218), job index (task.py:211). Add a private off-repo case log for end reason and §43 values (Charter:1397-1404), needed anyway since amounts are banned. H3 needs usage and an event, not this object (Charter:116). No new table: Task marker plus visibility branch, blurring ontology types (Charter:76). Real gaps: job Tasks visible to all active users (access.py:65-69), non-admin status changes (access.py:72-73), no "not approval" disclaimer. Insufficient only once others are active, mainly the seed project. Best counter: extraction needs a PV home (Charter:1914); that is sequencing, not failure evidence.
Q6 Kill criterion (PROPOSED): Remove creation and routes if, at H3 window end, no case has a dated flag preceding the scope check, §43 values logged and a founder-signed materiality note. Also at founder week 6 if over half of flags sat in To Check beyond 14 days. Hard stop 16 weeks after ship, so non-use cannot keep it alive. Thresholds have no baseline.
Verdict: insufficient-evidence
```

**Reviewer's limits:** no server or database access; did not read the private calibration data; Q5 rests on the Task model and its access rules alone.

**Proposer's additions to Q5:** the app's Task list cannot filter by Job; Task "Done" is its only closure and keeps no reason; and a Task used as a flag cannot carry the founder's rules (a Task is not required first, and Task completion must not change the commercial state).

## Founder adjudication (2026-11-07)

1. **Evidence.** Accepted knowingly: the commercial value is unverified. The founder first chose to wait for one real case (Option B), then replaced that with a GO for a minimum test version the same day. A simulated case is used for design and testing only; it is not a real case and not H3 evidence.
2. **Q5, the simpler alternative.** Not taken. A Task cannot carry the rules the founder set: Task Done would read as settling the commercial question, a Task keeps no end reason, and Tasks on a Job are visible to every active user.
3. **Scope approved** (DEC-PV-001, PRODUCT.md): flag from Site Log, find by Job, To Check / Following Up / Follow-up Ended with the reason kept, independent of Task, no quote, client approval, entitlement to charge or authority to start work.
4. **Sequence.** A separate exception to ADR-002 for this package only; the AI line stays paused (`ADR-002-exception-001-manual-potential-variation.md`).
5. **Reliability and permissions.** The save request is kept on the phone before it is sent and recovered under the same key. The flag pins the version the user confirmed. Flagger and admin can view; only the admin decides and reads the note; source text and attachments stay behind the entry's own rule. Real account roles are confirmed before release.
6. **Q6, the kill criterion.** Not adopted: no 16-week automatic removal. DEC-GATE-H3-001 stands as written: 6 weeks of founder use on a representative project; if no suitable event, at least one seed-builder project for 4 weeks; still no evidence, reassess. At that point the founder separates "no change happened", "it happened and nobody flagged it" and "it was flagged and did not help", because each leads to a different decision. Hiding the entry points is a later question; existing records stay.
7. **Q4, flags left sitting in To Check.** Acknowledged. This package shows the admin a To Check count on Home and opens the list on To Check; whether that is enough is part of the H3 reading, not assumed here.
