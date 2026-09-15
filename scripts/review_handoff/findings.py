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

import functools
import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

# The reviewer's protocol, vendored from the installed plugin and pinned by
# version and digest in protocol/PROTOCOL.json. CI has no plugin, so the
# vendored copy is what it validates against; a test compares the two
# wherever a plugin IS installed, so an upgrade surfaces as a failure rather
# than as silent drift.
PROTOCOL_DIR = Path(__file__).resolve().parent / "protocol"
PROTOCOL_RECORD = PROTOCOL_DIR / "PROTOCOL.json"


def canonical_digest(raw: bytes) -> str:
    """A digest of the schema's MEANING, not of its bytes.

    Git normalises line endings on checkout, so a raw-byte digest of a
    vendored file breaks the moment CI clones it. Canonical JSON is stable
    across that, across platforms and across reformatting, while still
    changing the instant the protocol itself does.
    """
    parsed = json.loads(raw.decode("utf-8"))
    return hashlib.sha256(
        json.dumps(parsed, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


@functools.lru_cache(maxsize=1)
def protocol() -> dict:
    """What protocol version this package validates against."""
    return json.loads(PROTOCOL_RECORD.read_text(encoding="utf-8"))


@functools.lru_cache(maxsize=1)
def protocol_schema() -> dict:
    return json.loads((PROTOCOL_DIR / protocol()["schema"]).read_text(encoding="utf-8"))


@functools.lru_cache(maxsize=1)
def _validator() -> Draft202012Validator:
    schema = protocol_schema()
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)

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
    # Set when this finding is the SAME defect another channel already
    # reported. Both records are kept - two channels seeing one thing is
    # evidence, not noise - but the group counts once.
    duplicate_of: str = ""
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


def _where(error: ValidationError) -> str:
    """The failing location, as a path a reader can follow."""
    path = "".join(
        f"[{part}]" if isinstance(part, int) else f".{part}" for part in error.absolute_path
    )
    return f"result{path}" if path else "result"


def validate_structured_result(result: object) -> dict:
    """Check a structured result against the plugin's OWN schema, or raise.

    Validation is the whole schema, by a standard JSON Schema validator:
    nested objects, array items, required fields, types, enums and bounds,
    with additionalProperties refused where the schema refuses them. An
    earlier version checked only the four top-level types, so a result whose
    next_steps held a null, or whose finding carried nothing but a severity
    and a title, was accepted and the missing fields were quietly defaulted
    on the way in.

    Nothing downstream repairs a value. If the result validates, every field
    the reader touches is present and of the promised type; if it does not,
    no verdict and no finding are extracted from it at all.
    """
    errors = sorted(_validator().iter_errors(result), key=lambda e: list(e.absolute_path))
    if not errors:
        return result
    first = errors[0]
    detail = "; ".join(f"{_where(e)}: {e.message}" for e in errors[:4])
    if len(errors) > 4:
        detail += f" (+{len(errors) - 4} more)"
    raise FindingsError(
        f"the reviewer's structured result does not satisfy "
        f"{protocol()['plugin']} {protocol()['plugin_version']}'s "
        f"{protocol()['schema']} — {detail}"
    ) from first


def findings_from_structured(
    result: dict | None,
    *,
    round_number: int,
    channel: str,
    recorded_at: str,
    existing: set[str] | None = None,
) -> list[Finding]:
    """Read the plugin's structured result, after validating all of it.

    A finding the schema promised but that cannot be read must not be
    silently dropped — dropping it is exactly how a blocking defect would
    disappear between the reviewer and the release condition. Nor may one be
    silently completed: a finding missing its body or its location is not a
    finding with an empty body, it is a protocol violation.
    """
    # Validated first, as a whole. Everything below therefore reads fields
    # the schema guarantees are present and correctly typed - no `or ""`, no
    # coercion, nothing that could turn an invalid field into a plausible
    # one on the way in.
    raw = validate_structured_result(result)["findings"]
    out: list[Finding] = []
    taken: set[str] = set(existing or ())
    for entry in raw:
        identifier = finding_id(
            round_number, channel, entry["title"], entry["file"], entry["line_start"], taken
        )
        taken.add(identifier)
        out.append(
            Finding(
                id=identifier,
                round=round_number,
                channel=channel,
                severity=entry["severity"],
                title=entry["title"],
                file=entry["file"],
                line_start=entry["line_start"],
                line_end=entry["line_end"],
                body=entry["body"],
                recommendation=entry["recommendation"],
                confidence=entry["confidence"],
                source=SOURCE_STRUCTURED,
                recorded_at=recorded_at,
            )
        )
    return out


def _primary_of(finding: Finding, by_id: dict[str, Finding]) -> Finding:
    primary, hops = finding, 0
    while primary.duplicate_of and primary.duplicate_of in by_id and hops < 16:
        primary = by_id[primary.duplicate_of]
        hops += 1
    return primary


def group_findings(findings: list[Finding]) -> dict[str, list[Finding]]:
    """Findings grouped by defect: the primary's id maps to its whole group."""
    by_id = {f.id: f for f in findings}
    groups: dict[str, list[Finding]] = {}
    for finding in findings:
        groups.setdefault(_primary_of(finding, by_id).id, []).append(finding)
    return groups


def unresolved_blocking(findings: list[Finding]) -> list[Finding]:
    """Blocking findings still open, counted once per defect.

    Two channels reporting one defect is evidence, not two problems. Both
    records are kept and both are reported, but the group counts once:
    counting them separately would overstate what is outstanding and demand
    the same fix be signed off twice.

    Within a group, blocking is whether ANY member is blocking - the lower
    severity of a second sighting does not soften the first - and resolved is
    whether the PRIMARY has been dispositioned, since that is the record the
    disposition is written on.
    """
    by_id = {f.id: f for f in findings}
    out: list[Finding] = []
    for primary_id, group in group_findings(findings).items():
        primary = by_id[primary_id]
        if any(f.blocking for f in group) and not primary.resolved:
            out.append(primary)
    return out


def duplicates_of(findings: list[Finding], identifier: str) -> list[Finding]:
    """The other channels' records of one defect."""
    return [f for f in findings if f.duplicate_of == identifier]


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
    review sat unread.

    Three states, three different answers. A channel that COMPLETED has a
    review someone must read. A channel that FAILED is a known nothing: the
    round is unusable on its own account and there is no output to triage. A
    channel whose outcome is UNKNOWN - the session died before it was
    recorded - is neither, and must not be quietly treated as the second: an
    unknown result is not evidence that nothing was found.
    """
    seen = {(f.round, f.channel) for f in findings}
    seen |= {(int(a["round"]), a["channel"]) for a in attestations}
    missing: list[str] = []
    for record in rounds:
        for channel in channels:
            status = channel_state(record, channel)
            if status == "failed":
                continue
            if (record.number, channel) in seen:
                continue
            if status == "completed":
                missing.append(f"round {record.number} channel {channel}")
            else:
                missing.append(
                    f"round {record.number} channel {channel} (outcome unknown - "
                    "the session ended before it was recorded)"
                )
    return missing


def channel_state(record, channel: str) -> str:
    """What is known about one channel of one round.

    Reads the recorded status when there is one, and otherwise infers it from
    the exit codes, so a run written before the status existed is judged the
    same way: a recorded exit code is a known outcome, and its absence is not.
    """
    status = (getattr(record, "channel_status", None) or {}).get(channel)
    if status in ("completed", "failed", "pending"):
        return status
    exit_codes = getattr(record, "exit_codes", None) or {}
    if channel not in exit_codes:
        return "pending"
    return "completed" if exit_codes[channel] == 0 else "failed"
