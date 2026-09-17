# Review input template

Everything the reviewer needs, in one argv element after `--`, on the
`adversarial-review` channel only. Fill the angle brackets; delete nothing else.

Keep it as one paragraph-per-block of plain prose. It travels as a single
argument, so it must contain no newline that a shell would act on, and it is
never split across argv elements.

---

```
Work package: <name>. Review the committed diff <BASE>..HEAD; HEAD is <HEAD SHA>
and the working tree is clean, so that range is the whole change under review.

Approved requirements: <one per clause, separated by " | ">.

Acceptance criteria: <what "done" means, separated by " | ">.

Approved change scope: <paths this package may touch, separated by " | ">.

Prohibited: <what it may not do, separated by " | ">.

Verification evidence: the gate was run against this exact head. Raw output:
<paths, and only the logs actually produced at this head>. Read the raw logs
rather than trusting any summary of them. <CI run id and its raw numbers, if a
run exists for this SHA.>

Judge the change on three things, in this order: CORRECTNESS (does it do what the
requirements say, including on failure paths, concurrency and partial failure);
MAINTAINABILITY (can the next person change it safely — naming, structure, tests
that would catch a regression, comments that explain why); and PROJECT FIT (does
it follow the conventions and architecture already in this repository rather than
importing a foreign pattern). Do not prefer a smaller change to a correct one,
and do not withhold a finding because the fix would be large.

Report only grounded defects: each needs file:line, a concrete scenario in which
it goes wrong, and what you would do about it.

If a change you consider NECESSARY falls outside the approved scope above, report
it anyway and prefix its title with OUT-OF-SCOPE. Say why it is necessary. Do not
suppress it, do not soften it into a suggestion, and do not treat the scope list
as a reason to stay silent. The run will stop and ask the founder for
authorisation; nothing outside the approved scope is changed automatically.

End with a single line 'Verdict: approve' or 'Verdict: needs-attention'. Approve
only if you found no defect that should block this package; if you are unsure,
say needs-attention.
```

---

## Two mistakes this template exists to prevent

1. **Claiming evidence that was produced at another head.** List only the logs
   from the gate run against the head being reviewed. Naming logs from earlier
   runs tells the reviewer that stale output verifies current code.
2. **Asking for what the call cannot deliver.** With an explicit `--base` the
   reviewer sees the commit range only. Do not ask it to consider uncommitted
   work; assert the tree is clean instead.

## The native channel

Takes no input at all — it rejects focus text. It is invoked with the same
`--base` and nothing after it. Its prose has to be read by a person; nothing
classifies it.
