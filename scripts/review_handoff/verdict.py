"""Turn a reviewer's answer into exactly one of three outcomes.

**The call contract is the rule.** Every review channel is invoked with
``--json``, so every channel owes a JSON envelope. That is what is read, and
it is the only thing read. Output that is not a JSON envelope — prose, a
truncated object, a banner followed by a broken object, anything at all —
yields no verdict. Neither does stderr. There is no text mode, no
"looks like JSON" test, and no compatibility path: those are how a
non-conforming answer ends up being read as an approval, which is precisely
what the envelope exists to prevent.

Within a valid envelope:

* the **adversarial** channel's result is validated against the installed
  plugin's structured protocol (``schemas/review-output.schema.json``), and
  its verdict is taken from that object's own enum — never from prose beside
  it;
* the **native** channel carries its review text inside the envelope, and
  that text is read for the agent to triage. It is never classified.

Everything ambiguous is ``unusable``, and unusable is never a pass — not a
crash, not a timeout, not an empty result, not a malformed envelope, and not
a channel that answered in some other shape.
"""

from __future__ import annotations

from dataclasses import dataclass

from .findings import FindingsError, validate_structured_result

APPROVE = "approve"
NEEDS_ATTENTION = "needs-attention"
UNUSABLE = "unusable"

# The plugin's own verdict enum, and nothing else. Widening this would be a
# compatibility mode by another name.
_STRUCTURED_VERDICTS = {"approve": APPROVE, "needs-attention": NEEDS_ATTENTION}

MIN_USEFUL_CHARS = 40


@dataclass(frozen=True)
class Verdict:
    value: str
    usable: bool
    reason: str

    @property
    def is_pass(self) -> bool:
        return self.usable and self.value == APPROVE


@dataclass(frozen=True)
class ChannelRead:
    """One channel's outcome, as the envelope reported it."""

    ok: bool
    reason: str
    verdict: Verdict | None
    structured: dict | None
    review_text: str


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


def _payload_text(envelope: dict) -> str:
    """The review text the envelope carries, if it carries any.

    The native channel keeps its prose in ``codex.stdout``; the adversarial
    channel repeats its structured result there and in ``rawOutput``. Nothing
    outside the envelope is consulted.
    """
    codex = envelope.get("codex")
    if isinstance(codex, dict) and isinstance(codex.get("stdout"), str):
        text = codex["stdout"].strip()
        if text:
            return text
    raw = envelope.get("rawOutput")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    result = envelope.get("result")
    if isinstance(result, dict) and isinstance(result.get("summary"), str):
        return result["summary"].strip()
    return ""


def _failure_detail(envelope: dict | None, raw_text: str = "") -> str:
    """Why the plugin failed, in its own words — DIAGNOSTIC ONLY.

    An exhausted quota, a broken login and a crashed reviewer all arrive as a
    non-zero exit with the cause somewhere in the output, and without this
    the run records only "review exited 1". Note what this is not: it runs
    solely on paths that have already failed, and nothing it returns can
    become a verdict. No approval is ever extracted from a stream.
    """
    codex = envelope.get("codex") if isinstance(envelope, dict) else None
    candidates = []
    if isinstance(envelope, dict):
        candidates.append(envelope.get("parseError"))
    if isinstance(codex, dict):
        candidates.extend([codex.get("stderr"), codex.get("stdout")])
    candidates.append(raw_text)
    for value in candidates:
        if isinstance(value, str) and value.strip():
            return " ".join(value.split())[:300]
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
    envelope: dict | None,
    raw_text: str,
    exit_code: int | None,
    timed_out: bool,
    expects_verdict: bool,
) -> ChannelRead:
    """Interpret one review channel. Every failure mode lands on not-ok.

    ``envelope`` is the parsed JSON object the call required, or ``None`` when
    stdout was not one. ``raw_text`` is used for diagnostics on already-failed
    paths and never for a verdict.
    """
    if timed_out:
        return _not_ok(
            "timed out before producing a result",
            "review timed out before producing a verdict",
            expects_verdict,
        )
    if exit_code is None:
        return _not_ok(
            "did not run",
            "review did not run",
            expects_verdict,
            _failure_detail(envelope, raw_text),
        )
    if exit_code != 0:
        return _not_ok(
            f"exit {exit_code}",
            f"review exited {exit_code}",
            expects_verdict,
            _failure_detail(envelope, raw_text),
        )

    if envelope is None:
        # The contract, enforced in one place. The call requested --json; an
        # answer in any other shape is not a review this workflow can read,
        # and nothing is salvaged from it - not a verdict line in the prose
        # around a broken object, not a banner, not stderr.
        return _not_ok(
            "the reviewer did not return a JSON envelope",
            "the call requested --json and stdout was not a JSON envelope; no "
            "verdict is taken from non-conforming output",
            expects_verdict,
        )

    text = _payload_text(envelope)

    if not expects_verdict:
        # The native channel reports in prose INSIDE the envelope. It is read
        # so the agent can triage it, and never classified here.
        if len(text.strip()) < MIN_USEFUL_CHARS:
            return ChannelRead(False, "produced no usable output", None, None, text)
        return ChannelRead(True, "completed", None, None, text)

    structured = envelope.get("result")
    try:
        validate_structured_result(structured)
    except FindingsError as exc:
        parse_error = envelope.get("parseError")
        note = f" ({parse_error})" if isinstance(parse_error, str) and parse_error else ""
        return _not_ok(
            "the reviewer's structured result does not match the plugin's protocol",
            f"the reviewer's structured result does not match the plugin's "
            f"protocol{note}: {exc}",
            expects_verdict,
        )

    # The schema has already confined this to its own enum. This map is the
    # separate question of what THIS workflow does with each value, and it
    # fails closed if a future protocol version adds one: a verdict nobody
    # has decided how to act on is not an approval.
    word = structured["verdict"].strip().lower()
    mapped = _STRUCTURED_VERDICTS.get(word)
    if mapped is None:  # pragma: no cover - unreachable until the enum widens
        return _not_ok(
            "a protocol verdict this workflow cannot act on",
            f"the protocol permits the verdict {structured['verdict']!r}, but this "
            "workflow has no rule for it; decide what it means before acting on it",
            expects_verdict,
        )
    verdict = Verdict(mapped, True, f"reviewer reported {mapped} (structured result)")
    return ChannelRead(True, verdict.reason, verdict, structured, text)
