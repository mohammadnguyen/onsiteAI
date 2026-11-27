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
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

STATE_FILENAME = "run.json"
# Bump when a change is INCOMPATIBLE: a field removed, a field whose meaning
# changes, or one added without a default. An unbumped incompatible change
# makes an older run die with a TypeError deep in the loader instead of the
# clear "start a new run" that load_state raises.
#
# A purely additive field WITH a default is compatible and must not be
# bumped for. Bumping orphans every run in flight, including the one doing
# the upgrade - which happened here: adding raw_digests as version 4 made
# the live run unreadable mid-flight, turning a safety rule into the outage
# it exists to prevent. An older record simply has no digests, and anything
# reading them treats "not recorded" as "not verifiable", never as "verified".
SCHEMA_VERSION = 3

# The oldest version this loader can read correctly. Version 3 added only
# defaulted fields (linked_run, findings, triage, events), so a version 2
# record loads with those empty and means exactly what it meant - which is
# why it is readable rather than refused.
#
# That bump should not have happened by the rule stated above, and refusing
# what it produced compounded the mistake: a run whose chain reached a
# version 2 ancestor could not read its history at all, and an unreadable
# ancestor is treated - correctly - as "there is history and nobody can read
# it", which blocks delivery. Raise this only for a change that genuinely
# alters what an older field MEANS; a newer version than this code knows is
# still refused, because there is no way to know what changed.
MIN_COMPATIBLE_SCHEMA = 2


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


def _under(path: Path, parents: tuple[Path, ...]) -> bool:
    for parent in parents:
        try:
            path.relative_to(parent)
        except ValueError:
            continue
        return True
    return False


def head_sha(repo_root: Path) -> str:
    return git_output(repo_root, "rev-parse", "HEAD")


def repo_toplevel(start: Path) -> Path:
    """The repository root, not whatever directory the command was run from.

    The untracked half of the tree fingerprint comes from ``git ls-files``,
    which lists relative to the CURRENT directory. Running the CLI from a
    subdirectory therefore fingerprinted only that subtree, and edits
    anywhere else left the digest unchanged - an approval would keep
    applying to a tree that had moved underneath it.
    """
    return Path(git_output(start, "rev-parse", "--show-toplevel"))


def dirty_paths(repo_root: Path, exclude: tuple[Path, ...] = ()) -> list[str]:
    """Paths with uncommitted work, ignoring the run's own directory.

    The reviewer is handed a COMMIT RANGE: the plugin resolves an explicit
    base to branch mode and collects ``git diff base..HEAD``, so anything not
    committed is invisible to it. Reviewing a dirty tree would therefore
    produce an approval describing code the reviewer never saw, while the
    tree digest happily bound that approval to the uncommitted version.
    """
    excluded = tuple(Path(p).resolve() for p in exclude)
    raw = git_bytes(repo_root, "status", "--porcelain", "-z", "--untracked-files=all")
    chunks = [c for c in raw.split(b"\0") if c]
    out: list[str] = []
    index = 0
    while index < len(chunks):
        entry = chunks[index]
        index += 1
        # Porcelain v1: two status characters, a space, then the path. The
        # status characters may themselves BE spaces (" M", "?? "), so this
        # is positional, never a split on whitespace.
        status, rel = entry[:2], os.fsdecode(entry[3:])
        if status[:1] in b"RC" and index < len(chunks):
            index += 1  # renames and copies carry their source in the next chunk
        if not rel:
            continue
        if excluded and _under((repo_root / rel).resolve(), excluded):
            continue
        out.append(rel)
    return sorted(out)


def resolve_commit(repo_root: Path, ref: str) -> str:
    """The full 40-character SHA a reference names, right now.

    A run stores the commit, never the name. "HEAD", a branch or a tag moves
    the moment anything is committed, so a base stored as a name silently
    re-points mid-run: the review would then cover a shrinking slice of the
    package while the recorded base still looked correct. Resolving once, at
    start, is what makes the base pinned in fact and not just in prose.
    """
    return git_output(repo_root, "rev-parse", "--verify", f"{ref}^{{commit}}")


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


def tree_digest(repo_root: Path, exclude: tuple[Path, ...] = ()) -> str:
    """A digest of everything a reviewer would see beyond the commit.

    Covers staged and unstaged changes to tracked files plus the list of
    untracked, non-ignored files and their contents. Two runs with the same
    head and the same digest are looking at the same tree; anything else is
    a different input, which is exactly when a previous verdict stops
    applying.

    ``exclude`` drops the run's OWN directory. A run directory inside the
    repository and not ignored otherwise becomes part of the fingerprint of
    the code it is measuring: every log the gate writes changes the digest,
    so the approval that gate supports is stale before it is recorded, and
    the run can never deliver. The run's artefacts are evidence about the
    tree, not part of it.
    """
    excluded = tuple(Path(p).resolve() for p in exclude)
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
        rel = os.fsdecode(rel_bytes)
        if excluded and _under((repo_root / rel).resolve(), excluded):
            continue
        digest.update(b"\0untracked\0")
        digest.update(rel_bytes)
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
    # sha256 of each archived channel log, taken when it was written. The
    # run directory is excluded from the tree fingerprint - it has to be, or
    # writing a log would invalidate the approval that log supports - so
    # deleting or rewriting the evidence is otherwise invisible to every
    # later check, and a delivery could claim raw output that is not there.
    #
    # Defaulted, so a run recorded before this existed still loads. Rounds
    # from such a run carry no digest and are reported as unverifiable
    # rather than as verified.
    raw_digests: dict[str, str] = field(default_factory=dict)
    # What is known about each channel: "pending" (started, outcome never
    # recorded), "completed" (exited 0) or "failed" (exited non-zero).
    # Written when the round is created and updated as each channel
    # finishes, so an interruption leaves the truth on disk instead of an
    # empty record that later reads as "nothing to account for".
    channel_status: dict[str, str] = field(default_factory=dict)


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
    # The run this one continues. A correction task is a NEW run - the old
    # one keeps its counts, its history and its stopped status untouched -
    # but the link is recorded so the pair reads as one story.
    linked_run: str = ""
    rounds: list[RoundRecord] = field(default_factory=list)
    gates: list[dict] = field(default_factory=list)
    # Per-finding dispositions and the agent's explicit triage of each prose
    # channel. See ``findings``; these are the release condition.
    findings: list[dict] = field(default_factory=list)
    triage: list[dict] = field(default_factory=list)
    # Anything done to the run that a later reader must know about, such as
    # an operator breaking a lock left by a crashed session.
    events: list[dict] = field(default_factory=list)
    # Dispositions this run records for findings raised by a run EARLIER in
    # the chain. The older record is never rewritten, so its own status stays
    # what it was; this is the separate half - the evidence that the item was
    # dealt with afterwards, and where.
    carried_resolutions: list[dict] = field(default_factory=list)

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

    def passing_round(
        self, repo_root: Path, exclude: tuple[Path, ...] = ()
    ) -> RoundRecord | None:
        """The most recent usable ``approve`` that still describes THIS tree.

        Returns ``None`` when there is no approval, when it was not usable,
        or when the head or working tree moved since it was produced.
        """
        last = self.last_round()
        if last is None or not last.usable or last.verdict != "approve":
            return None
        if last.head != head_sha(repo_root) or last.tree_digest != tree_digest(
            repo_root, exclude
        ):
            return None
        return last

    # ------------------------------------------------------------ storage
    def to_json(self) -> str:
        payload = asdict(self)
        return json.dumps(payload, indent=2, sort_keys=True) + "\n"

    def save(self, run_dir: Path) -> Path:
        """Write the state atomically.

        A partially written run.json is worse than a missing one: the loader
        reports corruption and the round counter, the stop reason and every
        finding disposition are gone. Writing a sibling file and renaming it
        into place makes the previous state survive a crash mid-write.
        """
        run_dir.mkdir(parents=True, exist_ok=True)
        path = run_dir / STATE_FILENAME
        fd, tmp_name = tempfile.mkstemp(dir=run_dir, prefix=".run-", suffix=".json")
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(self.to_json())
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        return path


@dataclass
class ChainWalk:
    """Every run behind this one, and anything wrong with the trail.

    ``problems`` is the point. A chain that cannot be followed - a link to a
    directory that is gone, a record that will not load, a cycle - must never
    look the same as a chain with nothing open in it. One means "there is no
    history to carry"; the other means "there is history and it cannot be
    read", and a delivery may not treat the second as the first.
    """

    runs: list[tuple[str, "RunState"]] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def linked_chain(state: RunState, limit: int = 64) -> ChainWalk:
    """Follow ``linked_run`` back as far as it goes.

    Returns the ancestors nearest-first, excluding the run it started from.
    Stops at the first problem and reports it rather than returning a partial
    history that reads as a complete one.
    """
    walk = ChainWalk()
    visited: set[str] = set()
    current = state
    while current.linked_run:
        if len(walk.runs) >= limit:
            walk.problems.append(
                f"the linked-run chain is longer than {limit} entries; refusing to "
                "follow it further"
            )
            return walk
        try:
            resolved = str(Path(current.linked_run).resolve())
        except (OSError, ValueError) as exc:  # pragma: no cover - exotic paths
            walk.problems.append(f"link {current.linked_run!r} is not a usable path: {exc}")
            return walk
        if resolved in visited:
            walk.problems.append(
                f"the linked-run chain loops back to {current.linked_run!r}; the "
                "history cannot be read as a sequence"
            )
            return walk
        visited.add(resolved)
        if not (Path(resolved) / STATE_FILENAME).exists():
            walk.problems.append(
                f"the run this chain continues ({current.linked_run}) is no longer "
                "there, so its open items cannot be read"
            )
            return walk
        try:
            older = load_state(Path(resolved))
        except StateError as exc:
            walk.problems.append(
                f"the run this chain continues ({current.linked_run}) cannot be "
                f"read ({exc}), so its open items cannot be read"
            )
            return walk
        walk.runs.append((resolved, older))
        current = older
    return walk


def load_state(run_dir: Path) -> RunState:
    path = Path(run_dir) / STATE_FILENAME
    try:
        payload = json.loads(_read_state_text(path))
    except OSError as exc:
        raise StateError(f"no run state at {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise StateError(f"run state at {path} is not valid JSON: {exc}") from exc
    version = payload.get("schema_version")
    if not isinstance(version, int) or not (
        MIN_COMPATIBLE_SCHEMA <= version <= SCHEMA_VERSION
    ):
        raise StateError(
            f"run state schema {version!r} is not supported (this code reads "
            f"{MIN_COMPATIBLE_SCHEMA} to {SCHEMA_VERSION}); start a new run rather "
            "than reusing it"
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


def _read_state_text(path: Path, attempts: int = 5) -> str:
    """Read the state file, retrying a momentary sharing failure.

    Saving replaces the file atomically, but on Windows a reader that opens
    it during the rename gets a sharing violation rather than either version.
    ``status`` deliberately takes no lock — it is a read-only view anyone may
    ask for — so without this a concurrent save turns an informational
    command into "no run state", which reads as a missing run.
    """
    last: OSError | None = None
    for attempt in range(attempts):
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise
        except OSError as exc:  # pragma: no cover - timing dependent
            last = exc
            time.sleep(0.05 * (attempt + 1))
    raise last  # pragma: no cover - only reached when every attempt failed


def artefact_path(run_dir: Path, recorded: str) -> Path:
    """Where an archived artefact actually is, given where the run is now.

    Paths are absolute from the moment they are recorded. Runs written before
    that stored them relative to whatever directory the CLI happened to run
    in, so resuming from anywhere else resolved them against the wrong root
    and reported present files as missing. Everything a run archives lives
    under its own directory, so such a path is rebased onto the directory we
    were actually given.
    """
    path = Path(recorded)
    if path.is_absolute():
        return path
    parts = path.parts
    name = Path(run_dir).name
    if name in parts:
        index = len(parts) - 1 - parts[::-1].index(name)
        return Path(run_dir).joinpath(*parts[index + 1:])
    return Path(run_dir) / path


def file_digest(path: Path) -> str:
    """sha256 of a file, or "" when it cannot be read."""
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return ""


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
    linked_run: str = "",
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
        linked_run=linked_run,
    )
