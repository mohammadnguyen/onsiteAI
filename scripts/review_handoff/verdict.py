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


# ----------------------------------------------------------------------
# Reading the plugin's STRUCTURED result.
#
# The adversarial channel is run against the plugin's own JSON output schema
# (schemas/review-output.schema.json: verdict, summary, findings[], next_steps),
# and ``--json`` returns that object inside the companion payload. Preferring
# it over the prose "Verdict:" line is not a nicety: the structured object
# also carries a severity per finding, which is what the release condition
# needs, and it takes a regex over model wording off the gating path.
#
# The prose parser above stays as the fallback for a plugin build that does
# not return a payload. Neither path guesses: with no structured verdict and
# no verdict line, the round is unusable.
# ----------------------------------------------------------------------

_STRUCTURED_VERDICTS = {"approve": APPROVE, "needs-attention": NEEDS_ATTENTION}


@dataclass(frozen=True)
class ChannelRead:
    """One channel's outcome, however the plugin chose to express it."""

    ok: bool
    reason: str
    verdict: Verdict | None
    structured: dict | None
    review_text: str


def _payload_text(payload: dict) -> str:
    """The prose a reader would see, whatever shape the payload has."""
    codex = payload.get("codex")
    if isinstance(codex, dict) and isinstance(codex.get("stdout"), str):
        text = codex["stdout"].strip()
        if text:
            return text
    raw = payload.get("rawOutput")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    result = payload.get("result")
    if isinstance(result, dict) and isinstance(result.get("summary"), str):
        return result["summary"].strip()
    return ""


def _failure_detail(payload: dict | None) -> str:
    """Why the plugin failed, in its own words.

    The plugin reports an authentication failure, an exhausted quota or a
    crashed reviewer inside its payload while still exiting non-zero. Without
    this the run records only "review exited 1", which sends the reader to
    the archive to learn something the run already knew - and the three
    causes need completely different responses.
    """
    if not isinstance(payload, dict):
        return ""
    for value in (
        payload.get("parseError"),
        (payload.get("codex") or {}).get("stderr") if isinstance(payload.get("codex"), dict) else None,
        (payload.get("codex") or {}).get("stdout") if isinstance(payload.get("codex"), dict) else None,
    ):
        if isinstance(value, str) and value.strip():
            detail = " ".join(value.split())
            return detail[:300]
    return ""


def _not_ok(
    reason: str, verdict_reason: str, expects_verdict: bool, detail: str = ""
) -> ChannelRead:
    if detail:
        reason = f"{reason}: {detail}"
        verdict_reason = f"{verdict_reason}: {detail}"
    return ChannelRead(
        False,
        reason,
        Verdict(UNUSABLE, False, verdict_reason) if expects_verdict else None,
        None,
        "",
    )


def read_channel(
    *,
    payload: dict | None,
    raw_text: str,
    exit_code: int | None,
    timed_out: bool,
    expects_verdict: bool,
) -> ChannelRead:
    """Interpret one review channel. Every failure mode lands on not-ok."""
    if timed_out:
        return _not_ok(
            "timed out before producing a result",
            "review timed out before producing a verdict",
            expects_verdict,
        )
    if exit_code is None:
        return _not_ok("did not run", "review did not run", expects_verdict)
    if exit_code != 0:
        return _not_ok(
            f"exit {exit_code}",
            f"review exited {exit_code}",
            expects_verdict,
            _failure_detail(payload),
        )

    structured = None
    text = raw_text
    parse_error = ""
    if payload is not None:
        candidate = payload.get("result")
        structured = candidate if isinstance(candidate, dict) else None
        text = _payload_text(payload) or raw_text
        if isinstance(payload.get("parseError"), str):
            parse_error = payload["parseError"]

    if not expects_verdict:
        # A channel that printed nothing has told us nothing, and treating
        # that as a completed review would let an incomplete round deliver.
        if len(text.strip()) < MIN_USEFUL_CHARS:
            return ChannelRead(False, "produced no usable output", None, structured, text)
        return ChannelRead(True, "completed", None, structured, text)

    if structured is not None and isinstance(structured.get("verdict"), str):
        word = structured["verdict"].strip().lower()
        mapped = _STRUCTURED_VERDICTS.get(word) or _classify(word)
        if mapped is None:
            verdict = Verdict(
                UNUSABLE,
                False,
                "the reviewer's structured result carries an unrecognised verdict "
                f"{structured['verdict']!r}",
            )
        else:
            verdict = Verdict(mapped, True, f"reviewer reported {mapped} (structured result)")
        return ChannelRead(verdict.usable, verdict.reason, verdict, structured, text)

    if payload is not None:
        # The plugin ran but returned nothing that fits its own schema.
        # Falling straight back to prose would reward exactly the failure the
        # schema exists to prevent, so only an explicit verdict line in the
        # surviving text is accepted, and its absence is unusable.
        fallback = parse_verdict(text, exit_code=exit_code, timed_out=False)
        if not fallback.usable:
            verdict = Verdict(
                UNUSABLE,
                False,
                "the reviewer returned no structured result"
                + (f" ({parse_error})" if parse_error else "")
                + " and no verdict line",
            )
            return ChannelRead(False, verdict.reason, verdict, None, text)
        return ChannelRead(True, fallback.reason, fallback, None, text)

    fallback = parse_verdict(text, exit_code=exit_code, timed_out=timed_out)
    return ChannelRead(fallback.usable, fallback.reason, fallback, structured, text)
