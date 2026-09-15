"""Running things: the repository's own gates, and the review plugin.

Both are subprocesses with a timeout, and both archive their output verbatim
before anything interprets it. The archived file — not a summary of it — is
the evidence a later reader checks.

Gate commands run STRICTLY IN SEQUENCE. Several of this repository's suites
share one PostgreSQL instance, and running them concurrently corrupts each
other's schema; that has already produced phantom failures here. Sequential
execution within a run is therefore a correctness requirement; exclusion
*between* runs is enforced separately, by the shared lock in ``locking``.

Two low-level properties matter throughout:

* **Bytes in, explicit decode out.** Child output is captured as bytes and
  decoded as UTF-8 here. Letting ``subprocess`` decode with the locale
  encoding mangles non-ASCII reviewer prose on Windows (cp1252) and can fail
  the capture outright on a character it cannot represent.
* **A timeout kills the whole tree.** ``subprocess``'s own timeout kills the
  direct child only, leaving pytest workers and node helpers running against
  the shared database after the run believed them dead. Only processes this
  run started are terminated; nothing else on the machine is touched.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path

from .process_tree import ContainmentError, ProcessTree, TreeKill

# A brief that says "python" means the interpreter running this workflow, not
# whatever happens to be first on PATH. Without this a run inside a virtual
# environment silently shells out to the system interpreter, which has none
# of the project's tooling installed — the failure looks like a broken gate
# rather than a misresolved command.
_PYTHON_NAMES = ("python", "python3", "py")


def resolve_argv(argv: list[str]) -> list[str]:
    """Pin a leading bare ``python`` to this interpreter; leave the rest alone."""
    if argv and argv[0].lower() in _PYTHON_NAMES:
        return [sys.executable, *argv[1:]]
    return list(argv)


def child_env(base: dict | None = None) -> dict:
    """Environment for a child process: force UTF-8 on its stdio.

    A Python child on Windows encodes its stdout with the ANSI code page when
    that stdout is a pipe, so a test name or an error message containing a
    curly quote arrives here already corrupted — decoding correctly at this
    end cannot undo that. These two variables make the child emit UTF-8 in
    the first place.

    They are SET, not defaulted. An inherited PYTHONIOENCODING (cp1252 from a
    shell profile, ascii from a CI image) would otherwise survive and break
    the capture, which is the failure this exists to prevent; what this
    process inherited says nothing about how the children it spawns should
    encode output it is about to read.
    """
    env = dict(os.environ if base is None else base)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


# The installed reviewer. Overridable so tests can point at an isolated fake
# and so a different plugin version can be selected without editing code.
DEFAULT_PLUGIN_SCRIPT = (
    Path.home()
    / ".claude/plugins/cache/openai-codex/codex/1.0.6/scripts/codex-companion.mjs"
)
PLUGIN_SCRIPT_ENV = "REVIEW_HANDOFF_PLUGIN"
PLUGIN_NODE_ENV = "REVIEW_HANDOFF_NODE"


@dataclass(frozen=True)
class CommandResult:
    argv: list[str]
    exit_code: int | None
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool
    log_path: Path | None = None
    killed_tree: bool = False
    kill_detail: str = ""
    # sha256 of the archived log, recorded when it is written. The run's own
    # directory is excluded from the tree fingerprint, so without this a
    # deleted or rewritten piece of evidence is invisible to every later
    # check.
    log_digest: str = ""

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    @property
    def combined(self) -> str:
        if self.stderr.strip():
            return f"{self.stdout}\n--- stderr ---\n{self.stderr}"
        return self.stdout

    def payload(self) -> dict | None:
        """The plugin's ``--json`` object, when the output is one.

        Only stdout is considered: progress notes and warnings land on stderr
        and would turn a valid payload into unparseable text.
        """
        text = self.stdout.strip()
        if not text.startswith("{"):
            return None
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None


def _decode(raw: bytes | None) -> str:
    if not raw:
        return ""
    return raw.decode("utf-8", "replace")


def run_command(
    argv: list[str],
    *,
    cwd: Path,
    timeout_seconds: float,
    log_path: Path | None = None,
    env: dict | None = None,
) -> CommandResult:
    """Run one command, archive it verbatim, never raise for a bad exit."""
    started = time.monotonic()
    timed_out = False
    kill = TreeKill()
    tree = ProcessTree()
    try:
        proc = subprocess.Popen(  # noqa: S603 - argv list, never a shell string
            argv,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=child_env(env),
            **tree.popen_kwargs(),
        )
    except OSError as exc:  # command not found, not executable, ...
        result = CommandResult(
            argv=list(argv),
            exit_code=None,
            stdout="",
            stderr=f"failed to start {argv[0]!r}: {exc}",
            duration_seconds=time.monotonic() - started,
            timed_out=False,
            log_path=log_path,
        )
        digest = _archive(result, cwd, b"", result.stderr.encode("utf-8"))
        return replace(result, log_digest=digest)

    try:
        tree.adopt(proc)
    except ContainmentError as exc:
        # Fail closed. A child that could not be contained must not be
        # allowed to run: its workers would be unreachable at timeout and
        # could outlive the gate's hold on the shared database.
        try:
            proc.kill()
            proc.communicate(timeout=30)
        except (OSError, subprocess.TimeoutExpired):  # pragma: no cover
            pass
        tree.close()
        result = CommandResult(
            argv=list(argv),
            exit_code=None,
            stdout="",
            stderr=f"refusing to run {argv[0]!r}: its process tree could not be "
            f"contained ({exc}), so a timeout could not reliably stop it",
            duration_seconds=time.monotonic() - started,
            timed_out=False,
            log_path=log_path,
        )
        digest = _archive(result, cwd, b"", result.stderr.encode("utf-8"))
        return replace(result, log_digest=digest)
    lost_output = False
    try:
        try:
            out, err = proc.communicate(timeout=max(1.0, timeout_seconds))
            exit_code = proc.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
            exit_code = None
            # The tree is killed FIRST, then the pipes are drained. Draining
            # first would wait on writers this call is about to terminate.
            kill = tree.kill(proc)
            try:
                out, err = proc.communicate(timeout=60)
            except subprocess.TimeoutExpired:  # pragma: no cover - pipes wedged
                out, err = b"", b""
                lost_output = True
    finally:
        # Closing the job also kills anything still inside it, so a command
        # that exited normally cannot leave a worker running against the
        # shared database.
        tree.close()
    duration = time.monotonic() - started

    stderr_text = _decode(err)
    if timed_out:
        outcome = "the process tree was terminated"
        if not kill.delivered:
            outcome = "the process tree could NOT be confirmed terminated"
            if kill.detail:
                outcome = f"{outcome} ({kill.detail})"
        note = f"\n--- killed after {timeout_seconds:.0f}s; {outcome} [{kill.method}] ---\n"
        if lost_output:
            note += (
                "--- the pipes did not close after the kill, so any output "
                "already produced was lost ---\n"
            )
        stderr_text += note
        err = (err or b"") + note.encode("utf-8")

    result = CommandResult(
        argv=list(argv),
        exit_code=exit_code,
        stdout=_decode(out),
        stderr=stderr_text,
        duration_seconds=duration,
        timed_out=timed_out,
        log_path=log_path,
        killed_tree=kill.delivered,
        kill_detail=kill.detail or kill.method,
    )
    digest = _archive(result, cwd, out or b"", err or b"")
    return replace(result, log_digest=digest)


def _archive(result: CommandResult, cwd: Path, out: bytes, err: bytes) -> str:
    """Write the child's bytes to the log unchanged, under a UTF-8 header.

    Returns the sha256 of what was written, so a later check can tell that
    the evidence is still the evidence.
    """
    if result.log_path is None:
        return ""
    result.log_path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        f"$ {' '.join(result.argv)}\n"
        f"cwd: {cwd}\n"
        f"exit: {result.exit_code}{' (TIMEOUT)' if result.timed_out else ''}\n"
        f"duration_seconds: {result.duration_seconds:.1f}\n"
        f"{'-' * 60}\n"
    ).encode("utf-8")
    body = out
    if err.strip():
        body = body + b"\n--- stderr ---\n" + err
    payload = header + body
    result.log_path.write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


def run_gate_commands(
    commands: list[list[str]],
    *,
    cwd: Path,
    log_dir: Path,
    timeout_seconds: float,
    budget_seconds: float | None = None,
) -> list[CommandResult]:
    """Run every verification command in order, stopping at the first failure.

    Sequential by construction — see the module docstring. Stopping early
    keeps the failing command's output at the end of the archive where a
    reader looks first, and avoids piling further failures on a broken
    database.
    """
    # The run's own deadline bounds every command, recomputed as the suite
    # proceeds: checking the budget only before the first command would let a
    # run with seconds left execute hours of tests.
    deadline = None if budget_seconds is None else time.monotonic() + budget_seconds
    results: list[CommandResult] = []
    for index, argv in enumerate(commands, start=1):
        slug = "-".join(argv[:3]).replace("/", "_").replace("\\", "_").replace(":", "")
        log_path = log_dir / f"gate-{index:02d}-{slug[:40]}.log"
        allowed = timeout_seconds
        if deadline is not None:
            allowed = min(allowed, deadline - time.monotonic())
            if allowed <= 0:
                # Archived like any other command: a step that produced no log
                # reads as a step that was never attempted.
                reason = (
                    "the run's total time budget was exhausted before this "
                    "command started; it was NOT run"
                )
                log_path.parent.mkdir(parents=True, exist_ok=True)
                log_path.write_bytes(
                    f"$ {' '.join(argv)}\ncwd: {cwd}\nexit: none (NOT RUN)\n"
                    f"{'-' * 60}\n{reason}\n".encode("utf-8")
                )
                results.append(
                    CommandResult(
                        argv=list(argv),
                        exit_code=None,
                        stdout="",
                        stderr=reason,
                        duration_seconds=0.0,
                        timed_out=True,
                        log_path=log_path,
                    )
                )
                break
        result = run_command(
            resolve_argv(argv),
            cwd=cwd,
            timeout_seconds=allowed,
            log_path=log_path,
        )
        results.append(result)
        if not result.ok:
            break
    return results


def plugin_script_path() -> Path:
    override = os.environ.get(PLUGIN_SCRIPT_ENV)
    return Path(override) if override else DEFAULT_PLUGIN_SCRIPT


# The plugin exposes two review channels and they do NOT agree in practice:
# in the package that motivated this workflow the native channel reported
# clean in four rounds where the adversarial channel found real defects, and
# in one round the native channel found a defect the adversarial channel
# never saw. Running one channel silently loses findings, so a round runs
# both, and neither channel's approval releases the other channel's findings
# (see ``findings``).
#
# Only the adversarial channel returns a machine-readable result: the plugin
# runs it against its own JSON output schema, so verdict and per-finding
# severities are READ rather than inferred. The native channel returns prose;
# it is archived and read by the agent, whose triage is recorded explicitly.
CHANNEL_VERDICT = "adversarial-review"
CHANNEL_FINDINGS = "review"
REVIEW_CHANNELS = (CHANNEL_VERDICT, CHANNEL_FINDINGS)


def build_review_argv(
    *,
    channel: str,
    base: str,
    focus: str,
    scope: str = "branch",
    plugin: Path | None = None,
) -> list[str]:
    """The exact reviewer invocation for one channel.

    Every flag and value is a SEPARATE argv element, and the focus text is
    one element after ``--``. This is not cosmetic. The plugin re-splits its
    arguments with a shell-like tokeniser when — and only when — it is handed
    a single raw string (``normalizeArgv``: ``argv.length === 1`` implies
    ``splitRawArgumentString``). An earlier version of this function packed
    the flags and the prose into one string, so that tokeniser consumed the
    backslashes in Windows paths, ate apostrophes in the approved
    requirements, and could read flag-like text inside the brief as an
    option. The reviewer was handed paths that do not exist.

    With separate elements the plugin's ``parseArgs`` runs directly on them,
    and the ``--`` terminator puts everything after it into positionals, so
    focus text that begins with a dash cannot be mistaken for a flag.

    Two of these flags are currently INERT, and are sent anyway. Say so
    plainly rather than justify them with behaviour the plugin does not have:

    * ``--wait`` — the review subcommands accept it and never read it;
      ``handleReviewCommand`` calls ``runForegroundCommand`` unconditionally,
      so a review already blocks until it finishes. It is sent because it
      states the requirement this workflow actually depends on: a
      backgrounded review would let the run read a result that does not
      exist yet. If a later plugin version starts honouring the flag, this
      call is already correct.
    * ``--scope`` — the plugin resolves an explicit ``--base`` to branch mode
      *before* it looks at the scope, so with a pinned base the scope cannot
      change anything. The review is therefore the commit range base..HEAD,
      which is why the run refuses to review a dirty tree: uncommitted work
      is invisible to the reviewer.

    ``--json`` is load-bearing: it asks for the plugin's structured payload,
    which carries the schema-constrained result.

    The native channel rejects focus text outright, so it receives only the
    flags — it is run for its findings, not for anything it is told.
    """
    plugin = plugin or plugin_script_path()
    node = os.environ.get(PLUGIN_NODE_ENV, "node")
    argv = [
        node,
        str(plugin),
        channel,
        "--wait",
        "--json",
        "--base",
        base,
        "--scope",
        scope,
    ]
    if channel == CHANNEL_VERDICT and focus:
        argv += ["--", focus]
    return argv


def invoke_review_round(
    *,
    base: str,
    focus: str,
    cwd: Path,
    raw_paths: dict[str, Path],
    timeout_seconds: float,
    scope: str = "branch",
    plugin: Path | None = None,
    on_channel_complete=None,
) -> dict[str, CommandResult]:
    """Run every review channel and archive each raw output before anyone
    reads it. The per-channel budget is split so one hanging channel cannot
    consume the whole round.

    ``on_channel_complete(channel, result)`` is called after each channel and
    BEFORE the next one starts. It is how the run records that channel's
    outcome and the digest of its archive: without it, a session killed
    during the second channel left the first one's archive on disk with
    nothing saying it had run, so what it reported became unknowable and the
    round read as having nothing to account for.
    """
    per_channel = max(1.0, timeout_seconds / len(REVIEW_CHANNELS))
    results: dict[str, CommandResult] = {}
    for channel in REVIEW_CHANNELS:
        argv = build_review_argv(
            channel=channel, base=base, focus=focus, scope=scope, plugin=plugin
        )
        results[channel] = run_command(
            argv,
            cwd=cwd,
            timeout_seconds=per_channel,
            log_path=raw_paths[channel],
        )
        if on_channel_complete is not None:
            on_channel_complete(channel, results[channel])
    return results
