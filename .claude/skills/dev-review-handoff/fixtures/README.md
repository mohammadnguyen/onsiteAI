# Fixtures — known reviewer outputs with the action they must produce

ADR-001 §5: a skill that has never caught a planted flaw is decoration. These
files are the planted flaws. Each is a reviewer response this workflow has to
handle, and `tests/test_review_handoff.py` asserts the **action the workflow
takes** for each one — proceed, block, or stop.

What is deliberately *not* asserted is reviewer prose. Only the explicit
`Verdict:` line is read; everything else is archived for a human to judge.
Building a classifier over model wording would put a second stochastic
component where a gate is supposed to be.

| File | Plants | Required action |
|---|---|---|
| `approve.txt` | a clean adversarial review | proceed — may finish as reviewed |
| `needs-attention.txt` | one grounded finding | proceed to the fix step; must NOT finish |
| `no-verdict.txt` | a review with no verdict line | block — unusable, never a pass |
| `conflicting.txt` | two verdicts in one response | block — unusable, never a pass |
| `empty.txt` | an empty response | block — unusable, never a pass |
| `native-clean.txt` | the findings channel, no machine verdict | archived; contributes findings only |

The findings channel (`codex review`) prints prose with no verdict line, so
it can never gate on its own. It still has to *run*: a round where it did not
complete is an incomplete review, and an incomplete review is not a pass.
