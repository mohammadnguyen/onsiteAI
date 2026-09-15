"""Development/review handoff (ADR-003).

The deterministic half of the workflow: validate the approved brief, run the
repository's own verification commands in sequence, hand a specific
base..head to the installed review plugin, archive every raw result, decide
whether a usable verdict exists, and keep the round and time budgets across
session restarts.

Judgement — implementing, deciding whether a reported defect is real, fixing
or refuting it — stays with the primary agent following
``.claude/skills/dev-review-handoff``. Nothing here merges or deploys.
"""

from .brief import Brief, BriefError, Limits, load_brief
from .state import RoundRecord, RunState, StateError, load_state, new_state
from .verdict import APPROVE, NEEDS_ATTENTION, UNUSABLE, Verdict, parse_verdict

__all__ = [
    "APPROVE",
    "NEEDS_ATTENTION",
    "UNUSABLE",
    "Brief",
    "BriefError",
    "Limits",
    "RoundRecord",
    "RunState",
    "StateError",
    "Verdict",
    "load_brief",
    "load_state",
    "new_state",
    "parse_verdict",
]
