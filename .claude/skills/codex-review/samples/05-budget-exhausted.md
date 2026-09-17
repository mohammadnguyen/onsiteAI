# Sample condition: the budget runs out with a finding still open

Not a reviewer output — a state the procedure reaches. Written down so the
handling is fixed in advance rather than improvised at the moment it is
inconvenient.

## The state

```
budget recorded at start : 1 initial review + 3 fix-and-review rounds, 4h elapsed
rounds used              : initial + 3   (all four reviews returned usable results)
time used                : 3h 40m of 4h
open                     : one high-severity finding, raised round 3, fix attempted
                           in round 4, reviewer still reports it in round 4
```

## The handling

**Stop.** The round budget is spent. Report:

- the finding, still open, with what was attempted and why it did not settle it
- every round's verdict and where its raw output is
- the gate result at the head that was delivered
- the time and rounds used against the budget recorded at the start

Deliver the Draft PR anyway, marked as not passing review, with the open item
named in the body. The founder decides what happens next.

## What must not happen

- **Do not start a fresh package to buy back rounds.** Restarting to reset the
  budget is exactly what the budget exists to prevent.
- **Do not treat "no rounds left" as approval.** An exhausted budget is a stop,
  not a pass, and the PR must not read as though the review concluded.
- **Do not extend the budget unilaterally.** Extending it is the founder's
  decision, asked for in the report.
- **Do not quietly drop the finding** because there is no round left to fix it
  in. An escaped defect gets a disposition, not silence.

## The honest limit

Nothing enforces this. No counter survives the session, and a later session that
does not read the recorded budget will not know it was spent. The defence is that
the budget and the rounds used are written into the PR body at every round, where
the next session and the founder can both see them.
