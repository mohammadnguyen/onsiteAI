"""Run state: what happened, how many rounds, and what the evidence binds to.

Everything here is deterministic bookkeeping (ADR-001 L1). Two properties
matter more than the rest:

* **The round counter survives.** It lives in a file, not in a session. A
  resumed or restarted session reloads the same counter, so "one initial
  review plus at most N automatic rounds" cannot be reset by starting over.
* **A verdict is bound to inputs.** Each review records the base and head
  commits and a digest of the working tree at the moment the reviewer read
  it. If any of those change, an earlier "approve" is reported as STALE —
  it described a different tree.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

STATE_FILENAME = "run.json"
# Bump whenever a stored field is added, removed or changes meaning. An
# unbumped change makes an older run die with a TypeError deep in the loader
# instead of the clear "start a new run" that load_state raises.
SCHEMA_VERSION = 2


class StateError(RuntimeError):
    """The run state is missing, unreadable, or internally inconsistent."""


def utc_now() -> datetime:
    return datetime.now(UTC)


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="seconds")


def _parse_iso(text: str) -> datetime:
    return datetime.fromisoformat(text)


def git_output(repo_root: Path, *args: str) -> str:
    """Run a read-only git command, or raise :class:`StateError`."""
    proc = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode != 0:
        raise StateError(
            f"git {' '.join(args)} failed ({proc.returncode}): {proc.stderr.strip()}"
        )
    return proc.stdout.strip()


def git_bytes(repo_root: Path, *args: str) -> bytes:
    """Read-only git, output kept as BYTES.

    Paths and diffs are byte strings that git does not promise are in the
    locale encoding. Letting subprocess decode them corrupted non-ASCII
    filenames here (cp1252 on Windows turned "café" into "cafÃ©"), which
    then failed to open — so decoding happens explicitly, with the
    filesystem's own encoding, only where a real path is needed.
    """
    proc = subprocess.run(
        ["git", *args], cwd=repo_root, capture_output=True, timeout=120
    )
    if proc.returncode != 0:
        raise StateError(
            f"git {' '.join(args)} failed ({proc.returncode}): "
            f"{proc.stderr.decode('utf-8', 'replace').strip()}"
        )
    return proc.stdout


def head_sha(repo_root: Path) -> str:
    return git_output(repo_root, "rev-parse", "HEAD")


def merge_base(repo_root: Path, ref: str) -> str | None:
    """The merge base of HEAD and ``ref``, or ``None`` when ``ref`` is
    unknown (an offline clone, a missing remote)."""
    try:
        return git_output(repo_root, "merge-base", "HEAD", ref)
    except StateError:
        return None


def is_ancestor(repo_root: Path, candidate: str, descendant: str = "HEAD") -> bool:
    proc = subprocess.run(
        ["git", "merge-base", "--is-ancestor", candidate, descendant],
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return proc.returncode == 0


def tree_digest(repo_root: Path) -> str:
    """A digest of everything a reviewer would see beyond the commit.

    Covers staged and unstaged changes to tracked files plus the list of
    untracked, non-ignored files and their contents. Two runs with the same
    head and the same digest are looking at the same tree; anything else is
    a different input, which is exactly when a previous verdict stops
    applying.
    """
    digest = hashlib.sha256()
    # A plain diff is enough, including for binaries. A review raised the
    # concern that "Binary files ... differ" is the same string however the
    # bytes change; checked, and that line is not the whole output - the
    # `index` line above it carries the post-image blob hash, so two
    # different edits to the same tracked binary produce different diffs.
    # `--binary` would also work but embeds the entire payload, which costs
    # real time on a large asset for no extra safety.
    digest.update(git_bytes(repo_root, "diff", "HEAD"))
    # -z: NUL-delimited and UNQUOTED. Without it git quotes and escapes any
    # non-ASCII path, and reading that literal name fails — which used to
    # hash a constant, so every later edit to such a file left the digest
    # unchanged and an old approval kept counting.
    raw = git_bytes(repo_root, "ls-files", "-z", "--others", "--exclude-standard")
    untracked = [chunk for chunk in raw.split(b"\0") if chunk]
    for rel_bytes in sorted(untracked):
        digest.update(b"\0untracked\0")
        digest.update(rel_bytes)
        rel = os.fsdecode(rel_bytes)
        try:
            digest.update((repo_root / rel).read_bytes())
        except OSError as exc:
            # Fail closed: a file that cannot be read cannot be shown to be
            # unchanged, so the run stops rather than approving a tree it
            # could not see.
            raise StateError(
                f"cannot read untracked file {rel!r} while fingerprinting the "
                f"working tree: {exc}"
            ) from exc
    return digest.hexdigest()


@dataclass
class RoundRecord:
    """One completed review attempt, including the ones that produced nothing.

    ``raw_paths`` holds one archived file per review channel, so the count of
    archived artefacts always equals rounds x channels. A round that produced
    fewer is an incomplete review by construction, not something a reader has
    to notice.
    """

    number: int
    kind: str  # "initial" or "auto"
    started_at: str
    finished_at: str
    base: str
    head: str
    tree_digest: str
    verdict: str  # approve | needs-attention | unusable
    usable: bool
    reason: str
    raw_paths: dict[str, str]
    exit_codes: dict[str, int | None]
    duration_seconds: float


@dataclass
class RunState:
    schema_version: int
    run_id: str
    brief_name: str
    brief_path: str
    brief_digest: str
    repo_root: str
    base: str
    started_at: str
    deadline_at: str
    max_review_rounds: int
    max_total_seconds: int
    status: str = "open"  # open | delivered | stopped
    stop_reason: str = ""
    rounds: list[RoundRecord] = field(default_factory=list)
    gates: list[dict] = field(default_factory=list)

    # -------------------------------------------------- derived properties
    @property
    def review_count(self) -> int:
        """Reviews actually performed, usable or not."""
        return len(self.rounds)

    @property
    def auto_rounds_used(self) -> int:
        """Automatic fix+re-review rounds consumed (the first review is not one)."""
        return max(0, self.review_count - 1)

    @property
    def auto_rounds_left(self) -> int:
        return max(0, self.max_review_rounds - self.auto_rounds_used)

    def seconds_left(self, now: datetime | None = None) -> float:
        now = now or utc_now()
        return (_parse_iso(self.deadline_at) - now).total_seconds()

    def last_round(self) -> RoundRecord | None:
        return self.rounds[-1] if self.rounds else None

    def gate_supports(self, record: RoundRecord) -> bool:
        """Whether the newest verification run passed on the SAME inputs the
        given review read. A gate re-run that failed afterwards — a flaky
        test, a broken dependency — must not be left behind a delivery."""
        if not self.gates:
            return False
        latest = self.gates[-1]
        return bool(
            latest.get("passed")
            and latest.get("head") == record.head
            and latest.get("tree_digest") == record.tree_digest
        )

    def passing_round(self, repo_root: Path) -> RoundRecord | None:
        """The most recent usable ``approve`` that still describes THIS tree.

        Returns ``None`` when there is no approval, when it was not usable,
        or when the head or working tree moved since it was produced.
        """
        last = self.last_round()
        if last is None or not last.usable or last.verdict != "approve":
            return None
        if last.head != head_sha(repo_root) or last.tree_digest != tree_digest(repo_root):
            return None
        return last

    # ------------------------------------------------------------ storage
    def to_json(self) -> str:
        payload = asdict(self)
        return json.dumps(payload, indent=2, sort_keys=True) + "\n"

    def save(self, run_dir: Path) -> Path:
        run_dir.mkdir(parents=True, exist_ok=True)
        path = run_dir / STATE_FILENAME
        path.write_text(self.to_json(), encoding="utf-8")
        return path


def load_state(run_dir: Path) -> RunState:
    path = Path(run_dir) / STATE_FILENAME
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise StateError(f"no run state at {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise StateError(f"run state at {path} is not valid JSON: {exc}") from exc
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise StateError(
            f"run state schema {payload.get('schema_version')!r} is not supported "
            f"(expected {SCHEMA_VERSION}); start a new run rather than reusing it"
        )
    rounds = [RoundRecord(**r) for r in payload.pop("rounds", [])]
    try:
        return RunState(**payload, rounds=rounds)
    except TypeError as exc:
        # Belt and braces: a file that passed the version check but still does
        # not fit the dataclass is corrupt, not a crash site.
        raise StateError(
            f"run state at {path} does not match this schema: {exc}"
        ) from exc


def text_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def new_state(
    *,
    run_id: str,
    brief_name: str,
    brief_path: Path,
    brief_digest: str,
    repo_root: Path,
    base: str,
    max_review_rounds: int,
    max_total_seconds: int,
    now: datetime | None = None,
) -> RunState:
    started = now or utc_now()
    deadline = started.timestamp() + max_total_seconds
    return RunState(
        schema_version=SCHEMA_VERSION,
        run_id=run_id,
        brief_name=brief_name,
        brief_path=str(brief_path),
        brief_digest=brief_digest,
        repo_root=str(repo_root),
        base=base,
        started_at=_iso(started),
        deadline_at=_iso(datetime.fromtimestamp(deadline, tz=UTC)),
        max_review_rounds=max_review_rounds,
        max_total_seconds=max_total_seconds,
    )
