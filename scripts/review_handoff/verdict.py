"""Turn a reviewer's raw output into exactly one of three answers.

Deliberately dumb. The reviewer is a language model, and building a scoring
system over its prose would be a second stochastic component pretending to
be a gate. Instead the plugin's own explicit verdict line is the only thing
read, with two rules:

* exactly one recognised verdict marker must be present — zero markers, or
  two that disagree, is ``unusable``;
* ``unusable`` is never a pass. Neither is a crash, a timeout, an empty
  file, or a non-zero exit. The run treats all of them the same way: no
  usable review happened, so nothing may be reported as reviewed.

Findings are NOT parsed. The primary agent reads the raw text and decides;
that judgement is its job, not this module's.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

APPROVE = "approve"
NEEDS_ATTENTION = "needs-attention"
UNUSABLE = "unusable"

# The companion plugin prints one "Verdict: <word>" line per review. Both
# spellings of the negative verdict are accepted because the plugin has used
# both; anything else is not guessed at.
_VERDICT_LINE = re.compile(
    r"^\s*\**\s*verdict\s*\**\s*[:=]\s*\**\s*([a-z][a-z -]*?)\s*\**\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_APPROVE_WORDS = {"approve", "approved", "pass", "passed"}
_ATTENTION_WORDS = {
    "needs-attention",
    "needs attention",
    "needs-work",
    "needs work",
    "request-changes",
    "request changes",
    "changes-requested",
    "changes requested",
    "reject",
    "rejected",
    "block",
    "blocked",
}
MIN_USEFUL_CHARS = 40


@dataclass(frozen=True)
class Verdict:
    value: str
    usable: bool
    reason: str

    @property
    def is_pass(self) -> bool:
        return self.usable and self.value == APPROVE


def _classify(word: str) -> str | None:
    word = word.strip().lower()
    if word in _APPROVE_WORDS:
        return APPROVE
    if word in _ATTENTION_WORDS:
        return NEEDS_ATTENTION
    return None


def combine_round(
    verdict_channel: Verdict,
    findings_channel_ok: bool,
    findings_channel_reason: str,
    findings_channel_text: str | None = None,
) -> Verdict:
    """One answer for a round that ran several channels.

    Fails closed on every axis: the channel carrying the machine-readable
    verdict must be usable AND the findings channel must have completed. A
    round where one channel never produced output is an incomplete review,
    which is not a pass — the agent has not seen everything the reviewer
    would have said.
    """
    if not verdict_channel.usable:
        return verdict_channel
    if not findings_channel_ok:
        return Verdict(
            UNUSABLE,
            False,
            f"the findings channel did not complete ({findings_channel_reason}); "
            "the review is incomplete",
        )
    # Exiting 0 with nothing to say is not the same as having reviewed: a
    # channel that printed nothing has told us nothing, and treating that as
    # a completed review would let an incomplete round deliver.
    if findings_channel_text is None or len(findings_channel_text.strip()) < MIN_USEFUL_CHARS:
        return Verdict(
            UNUSABLE,
            False,
            "the findings channel produced no usable output; the review is incomplete",
        )
    return verdict_channel


def parse_verdict(
    raw: str | None,
    *,
    exit_code: int | None = 0,
    timed_out: bool = False,
) -> Verdict:
    """Classify one review attempt. Every failure mode lands on ``unusable``."""
    if timed_out:
        return Verdict(UNUSABLE, False, "review timed out before producing a verdict")
    if exit_code is None:
        return Verdict(UNUSABLE, False, "review did not run")
    if exit_code != 0:
        return Verdict(UNUSABLE, False, f"review exited {exit_code}")
    if raw is None:
        return Verdict(UNUSABLE, False, "no review output was captured")
    if len(raw.strip()) < MIN_USEFUL_CHARS:
        return Verdict(UNUSABLE, False, "review output is too short to be a review")

    found = {
        classified
        for match in _VERDICT_LINE.finditer(raw)
        if (classified := _classify(match.group(1))) is not None
    }
    if not found:
        return Verdict(
            UNUSABLE,
            False,
            "no recognised verdict line in the review output",
        )
    if len(found) > 1:
        return Verdict(
            UNUSABLE,
            False,
            f"review output carries conflicting verdicts: {sorted(found)}",
        )
    value = found.pop()
    return Verdict(value, True, f"reviewer reported {value}")
