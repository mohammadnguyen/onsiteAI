"""Running things: the repository's own gates, and the review plugin.

Both are subprocesses with a timeout, and both archive their output verbatim
before anything interprets it. The archived file — not a summary of it — is
the evidence a later reader checks.

Gate commands run STRICTLY IN SEQUENCE. Several of this repository's suites
share one PostgreSQL instance, and running them concurrently corrupts each
other's schema; that has already produced phantom failures here. Sequential
execution is therefore a correctness requirement, not a preference.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

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

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    @property
    def combined(self) -> str:
        if self.stderr.strip():
            return f"{self.stdout}\n--- stderr ---\n{self.stderr}"
        return self.stdout


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
    try:
        proc = subprocess.run(
            argv,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=max(1.0, timeout_seconds),
            env=env,
        )
        exit_code, stdout, stderr = proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        exit_code = None
        stdout = exc.stdout if isinstance(exc.stdout, str) else (exc.stdout or b"").decode(
            "utf-8", "replace"
        )
        stderr = exc.stderr if isinstance(exc.stderr, str) else (exc.stderr or b"").decode(
            "utf-8", "replace"
        )
    except OSError as exc:  # command not found, not executable, ...
        timed_out = False
        exit_code = None
        stdout = ""
        stderr = f"failed to start {argv[0]!r}: {exc}"
    duration = time.monotonic() - started

    result = CommandResult(
        argv=list(argv),
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        duration_seconds=duration,
        timed_out=timed_out,
        log_path=log_path,
    )
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        header = (
            f"$ {' '.join(argv)}\n"
            f"cwd: {cwd}\n"
            f"exit: {exit_code}{' (TIMEOUT)' if timed_out else ''}\n"
            f"duration_seconds: {duration:.1f}\n"
            f"{'-' * 60}\n"
        )
        log_path.write_text(header + result.combined, encoding="utf-8")
    return result


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
                results.append(
                    CommandResult(
                        argv=list(argv),
                        exit_code=None,
                        stdout="",
                        stderr="the run's total time budget was exhausted before "
                        "this command started",
                        duration_seconds=0.0,
                        timed_out=True,
                        log_path=None,
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
# never saw (it was not run). Running one channel silently loses findings,
# so a round runs both.
#
# Only the adversarial channel prints an explicit "Verdict:" line, so it is
# the one a machine may gate on. The native channel's output is prose; it is
# archived and read by the agent, and its ABSENCE fails the round, but its
# content is never classified — pattern-matching a reviewer's wording would
# be a second stochastic component pretending to be a gate.
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

    ``--wait`` is mandatory: a backgrounded review would let the run
    continue against a result that does not exist yet. The native channel
    takes no focus text, so it receives only the binding flags.
    """
    plugin = plugin or plugin_script_path()
    node = os.environ.get(PLUGIN_NODE_ENV, "node")
    flags = f"--wait --base {base} --scope {scope}"
    if channel == CHANNEL_VERDICT and focus:
        flags = f"{flags} {focus}"
    return [node, str(plugin), channel, flags.strip()]


def invoke_review_round(
    *,
    base: str,
    focus: str,
    cwd: Path,
    raw_paths: dict[str, Path],
    timeout_seconds: float,
    scope: str = "branch",
    plugin: Path | None = None,
) -> dict[str, CommandResult]:
    """Run every review channel and archive each raw output before anyone
    reads it. The per-channel budget is split so one hanging channel cannot
    consume the whole round."""
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
    return results
