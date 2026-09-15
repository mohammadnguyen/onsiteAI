"""Task brief: the approved inputs a handoff run is allowed to act on.

The brief is the only place a run learns what was approved. It is written by
the founder (or by the primary agent and confirmed by the founder) BEFORE a
run starts, and it is archived verbatim with the run, so what a reviewer
sees later is what the run was actually bound to.

Format is TOML — already a dependency-free stdlib parse on 3.11+, and it
keeps multi-line prose readable. Every field below is REQUIRED and must be
non-empty: a brief that forgets to say what is forbidden, or how to verify,
is not an approval, and the run refuses to start rather than inventing one.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .locking import DEFAULT_SHARED_LOCK_NAME

# Defaults for the run limits. Both are configurable per brief; the CLI can
# lower them further but never raise them above what the brief approved.
DEFAULT_MAX_REVIEW_ROUNDS = 3  # automatic fix+re-review rounds AFTER the first review
DEFAULT_MAX_TOTAL_SECONDS = 4 * 60 * 60


class BriefError(ValueError):
    """The brief is missing, unreadable, or does not carry an approval."""


@dataclass(frozen=True)
class Limits:
    max_review_rounds: int = DEFAULT_MAX_REVIEW_ROUNDS
    max_total_seconds: int = DEFAULT_MAX_TOTAL_SECONDS
    # Runs that touch the same external resource must not verify at the same
    # time. Two runs sharing this NAME queue behind one machine-wide lock;
    # a package whose suites use a private database can give it its own name
    # and stop queueing behind everyone else.
    shared_lock: str = DEFAULT_SHARED_LOCK_NAME


@dataclass(frozen=True)
class Brief:
    """One approved work package."""

    name: str
    requirements: list[str]
    acceptance_criteria: list[str]
    allowed_paths: list[str]
    prohibitions: list[str]
    verification_commands: list[list[str]]
    limits: Limits = field(default_factory=Limits)
    source_path: Path | None = None
    raw_text: str = ""

    def to_public_dict(self) -> dict:
        """What the reviewer is told the run was approved to do."""
        return {
            "name": self.name,
            "requirements": self.requirements,
            "acceptance_criteria": self.acceptance_criteria,
            "allowed_paths": self.allowed_paths,
            "prohibitions": self.prohibitions,
            "verification_commands": [" ".join(c) for c in self.verification_commands],
            "limits": {
                "max_review_rounds": self.limits.max_review_rounds,
                "max_total_seconds": self.limits.max_total_seconds,
                "shared_lock": self.limits.shared_lock,
            },
        }


_REQUIRED_LISTS = (
    "requirements",
    "acceptance_criteria",
    "allowed_paths",
    "prohibitions",
)


def _string_list(data: dict, key: str) -> list[str]:
    value = data.get(key)
    if not isinstance(value, list) or not value:
        raise BriefError(f"brief field {key!r} must be a non-empty list of strings")
    out = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise BriefError(f"brief field {key!r} contains a non-string or empty entry")
        out.append(item.strip())
    return out


def _commands(data: dict) -> list[list[str]]:
    value = data.get("verification_commands")
    if not isinstance(value, list) or not value:
        raise BriefError(
            "brief field 'verification_commands' must be a non-empty list of "
            "argument lists, e.g. [['python', '-m', 'pytest', '-q']]"
        )
    out: list[list[str]] = []
    for entry in value:
        # Argument lists only: a shell string would invite quoting bugs and
        # make the archived command ambiguous.
        if not isinstance(entry, list) or not entry:
            raise BriefError(
                "each verification command must be a non-empty list of arguments"
            )
        argv = []
        for arg in entry:
            if not isinstance(arg, str) or not arg:
                raise BriefError("verification command arguments must be non-empty strings")
            argv.append(arg)
        out.append(argv)
    return out


def _limits(data: dict) -> Limits:
    raw = data.get("limits", {})
    if not isinstance(raw, dict):
        raise BriefError("brief field 'limits' must be a table")
    rounds = raw.get("max_review_rounds", DEFAULT_MAX_REVIEW_ROUNDS)
    seconds = raw.get("max_total_seconds", DEFAULT_MAX_TOTAL_SECONDS)
    if not isinstance(rounds, int) or isinstance(rounds, bool) or rounds < 0:
        raise BriefError("limits.max_review_rounds must be an integer >= 0")
    if not isinstance(seconds, int) or isinstance(seconds, bool) or seconds <= 0:
        raise BriefError("limits.max_total_seconds must be a positive integer")
    lock = raw.get("shared_lock", DEFAULT_SHARED_LOCK_NAME)
    if not isinstance(lock, str) or not lock.strip():
        raise BriefError("limits.shared_lock must be a non-empty string")
    return Limits(
        max_review_rounds=rounds,
        max_total_seconds=seconds,
        shared_lock=lock.strip(),
    )


def load_brief(path: str | Path) -> Brief:
    """Parse and validate a brief. Raises :class:`BriefError` on anything
    that would leave the run guessing what it was allowed to do."""
    path = Path(path)
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise BriefError(f"cannot read brief {path}: {exc}") from exc
    try:
        data = tomllib.loads(raw_text)
    except tomllib.TOMLDecodeError as exc:
        raise BriefError(f"brief {path} is not valid TOML: {exc}") from exc

    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        raise BriefError("brief field 'name' must be a non-empty string")

    fields = {key: _string_list(data, key) for key in _REQUIRED_LISTS}
    return Brief(
        name=name.strip(),
        verification_commands=_commands(data),
        limits=_limits(data),
        source_path=path,
        raw_text=raw_text,
        **fields,
    )
