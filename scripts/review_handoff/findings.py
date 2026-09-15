"""Findings and their dispositions — the release condition of a run.

A verdict alone is not a release condition. The reviewer has two channels and
they disagree in practice, so an ``approve`` on one channel says nothing
about a blocking defect the other channel reported. This module records every
finding individually, with what happened to it, and the run refuses to
deliver while any blocking one is unresolved — whichever channel raised it.

What is NOT here, on purpose:

* **No second judge.** Nothing scores, ranks or re-interprets reviewer prose.
  The adversarial channel already returns a machine-readable object (the
  plugin constrains it with its own JSON schema), so severities and titles
  are *read*, not inferred. Everything else is recorded by the agent as an
  explicit act with a reason attached.
* **No automatic resolution.** A finding becomes ``fixed`` or ``refuted``
  only when something says so on the record. Silence leaves it ``pending``,
  and pending blocks.

Dispositions:

``pending``                the default; nothing has been decided
``fixed``                  changed in the tree, with the change identified
``refuted``                shown to be wrong, with the evidence identified
``awaiting-adjudication``  real, and needs the founder (out of approved
                           scope, or a conflict with the brief)
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass

SEVERITIES = ("critical", "high", "medium", "low")
# Severities that stop a delivery. The plugin's own schema uses exactly these
# four words, so this is a direct reading of its output, not a scale invented
# here.
BLOCKING_SEVERITIES = frozenset({"critical", "high"})

PENDING = "pending"
FIXED = "fixed"
REFUTED = "refuted"
AWAITING = "awaiting-adjudication"
DISPOSITIONS = (PENDING, FIXED, REFUTED, AWAITING)
RESOLVED_DISPOSITIONS = frozenset({FIXED, REFUTED})

SOURCE_STRUCTURED = "structured"  # read from the plugin's JSON result
SOURCE_AGENT = "agent"  # recorded by the agent from a prose channel


class FindingsError(ValueError):
    """A finding record is malformed or refers to something that is not there."""


@dataclass
class Finding:
    id: str
    round: int
    channel: str
    severity: str
    title: str
    file: str = ""
    line_start: int | None = None
    line_end: int | None = None
    body: str = ""
    recommendation: str = ""
    confidence: float | None = None
    source: str = SOURCE_STRUCTURED
    out_of_scope: bool = False
    disposition: str = PENDING
    note: str = ""
    recorded_at: str = ""
    resolved_at: str = ""

    @property
    def blocking(self) -> bool:
        """Blocking means a delivery may not pass over it.

        Severity is the usual reason. Out-of-scope is the other: a change the
        reviewer says is necessary but the brief did not approve cannot be
        made by this run at any severity, so it needs the founder before the
        package can be called done.
        """
        return self.severity in BLOCKING_SEVERITIES or self.out_of_scope

    @property
    def resolved(self) -> bool:
        return self.disposition in RESOLVED_DISPOSITIONS

    def one_line(self) -> str:
        where = self.file or "-"
        if self.line_start:
            where = f"{where}:{self.line_start}"
        flag = " OUT-OF-SCOPE" if self.out_of_scope else ""
        return (
            f"{self.id}  [{self.severity}{flag}] {self.title} ({where}) "
            f"[{self.channel}] -> {self.disposition}"
            + (f": {self.note}" if self.note else "")
        )

    def to_dict(self) -> dict:
        return asdict(self)


def finding_id(
    round_number: int,
    channel: str,
    title: str,
    file: str,
    line: int | None,
    taken: set[str] | None = None,
) -> str:
    """A stable id, so the same finding keeps its disposition across commands.

    ``taken`` disambiguates a genuine collision. Two findings that share a
    round, a channel, a title, a file and a line hash identically, and a
    shared id is not a cosmetic problem: only the first is ever addressable,
    so the second can never be resolved and the run can never deliver. A
    reviewer reporting the same title at the same line twice - the same rule
    broken in two places it did not distinguish - is not exotic.
    """
    key = f"{round_number}|{channel}|{title.strip().lower()}|{file.strip()}|{line or 0}"
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:8]
    base = f"r{round_number:02d}-{channel.split('-')[0][:4]}-{digest}"
    if taken is None or base not in taken:
        return base
    for suffix in range(2, 100):
        candidate = f"{base}-{suffix}"
        if candidate not in taken:
            return candidate
    raise FindingsError(f"cannot allocate a unique id for finding {base!r}")


def _clean_severity(value: object) -> str:
    text = str(value or "").strip().lower()
    if text not in SEVERITIES:
        raise FindingsError(
            f"severity {value!r} is not one of {', '.join(SEVERITIES)}; the "
            "plugin's own schema uses exactly these words"
        )
    return text


def _clean_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def findings_from_structured(
    result: dict | None,
    *,
    round_number: int,
    channel: str,
    recorded_at: str,
    existing: set[str] | None = None,
) -> list[Finding]:
    """Read the plugin's structured result. Malformed entries are an error.

    A finding the schema promised but that cannot be read must not be
    silently dropped — dropping it is exactly how a blocking defect would
    disappear between the reviewer and the release condition.
    """
    if not isinstance(result, dict):
        return []
    raw = result.get("findings")
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise FindingsError("the reviewer's 'findings' field is not a list")
    out: list[Finding] = []
    taken: set[str] = set(existing or ())
    for index, entry in enumerate(raw, start=1):
        if not isinstance(entry, dict):
            raise FindingsError(f"finding #{index} in the reviewer's result is not an object")
        title = str(entry.get("title") or "").strip()
        if not title:
            raise FindingsError(f"finding #{index} in the reviewer's result has no title")
        file = str(entry.get("file") or "").strip()
        line_start = _clean_int(entry.get("line_start"))
        confidence = entry.get("confidence")
        identifier = finding_id(round_number, channel, title, file, line_start, taken)
        taken.add(identifier)
        out.append(
            Finding(
                id=identifier,
                round=round_number,
                channel=channel,
                severity=_clean_severity(entry.get("severity")),
                title=title,
                file=file,
                line_start=line_start,
                line_end=_clean_int(entry.get("line_end")),
                body=str(entry.get("body") or "").strip(),
                recommendation=str(entry.get("recommendation") or "").strip(),
                confidence=float(confidence) if isinstance(confidence, (int, float)) else None,
                source=SOURCE_STRUCTURED,
                recorded_at=recorded_at,
            )
        )
    return out


def unresolved_blocking(findings: list[Finding]) -> list[Finding]:
    return [f for f in findings if f.blocking and not f.resolved]


def untriaged_channels(
    *, rounds: list, findings: list[Finding], attestations: list[dict], channels: tuple[str, ...]
) -> list[str]:
    """Channels that produced a review nobody has accounted for.

    A channel is accounted for when it produced structured findings that were
    read automatically, or when the agent recorded findings from it, or when
    the agent explicitly attested that it reported none. Absence of findings
    is otherwise indistinguishable from nobody having looked, and this
    workflow may not treat those as the same thing.

    Judged PER CHANNEL, not per round. Usability is a whole-round verdict
    that fails closed on any channel, so an earlier version skipped every
    channel of an unusable round - including one that exited 0 and archived a
    complete review. A later approving round could then deliver while that
    review sat unread. A channel is therefore triageable exactly when it
    exited 0, whatever the round's own verdict was.
    """
    seen = {(f.round, f.channel) for f in findings}
    seen |= {(int(a["round"]), a["channel"]) for a in attestations}
    missing: list[str] = []
    for record in rounds:
        exit_codes = getattr(record, "exit_codes", None) or {}
        for channel in channels:
            if exit_codes.get(channel) != 0:
                # The channel did not complete, so there is no review to read.
                continue
            if (record.number, channel) not in seen:
                missing.append(f"round {record.number} channel {channel}")
    return missing
