"""Command line for one handoff run.

    python -m scripts.review_handoff start    --brief <file> [--run-dir <dir>]
    python -m scripts.review_handoff gate     --run-dir <dir>
    python -m scripts.review_handoff review   --run-dir <dir> [--focus-file <file>]
    python -m scripts.review_handoff findings <record|none|resolve|list> --run-dir <dir>
    python -m scripts.review_handoff status   --run-dir <dir> [--json]
    python -m scripts.review_handoff finish   --run-dir <dir> [--force]
    python -m scripts.review_handoff stop     --run-dir <dir> --reason <text>

Exit codes (see ``console``): 0 proceed, 1 blocked, 2 misuse.

Every command that writes state takes the run's lock first, and ``gate``
additionally takes a machine-wide lock named by the brief, because the
verification suites of different runs share one database. A held lock is
reported, never forced: no other session or runtime is ever stopped.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from .brief import Brief, BriefError, load_brief
from .cli_triage import add_findings_parser
from .console import EXIT_BLOCKED, EXIT_MISUSE, EXIT_OK, emit
from .findings import (
    AWAITING,
    Finding,
    FindingsError,
    findings_from_structured,
    unresolved_blocking,
    untriaged_channels,
)
from .locking import (
    DEFAULT_GATE_LOCK_WAIT_SECONDS,
    FileLock,
    LockBusy,
    run_lock_path,
    shared_lock_path,
)
from .prompt import OUT_OF_SCOPE_MARKER, review_instructions
from .runner import (
    CHANNEL_FINDINGS,
    CHANNEL_VERDICT,
    REVIEW_CHANNELS,
    invoke_review_round,
    run_gate_commands,
)
from .state import (
    RoundRecord,
    RunState,
    StateError,
    artefact_path,
    dirty_paths,
    file_digest,
    git_output,
    head_sha,
    is_ancestor,
    load_state,
    merge_base,
    new_state,
    repo_toplevel,
    resolve_commit,
    text_digest,
    tree_digest,
    utc_now,
)
from .verdict import combine_round, read_channel

# Re-exported: the skill and the tests branch on these names.
__all__ = ["EXIT_OK", "EXIT_BLOCKED", "EXIT_MISUSE", "main", "build_parser"]

_emit = emit

DEFAULT_RUNS_ROOT = Path(".claude/handoff")
GATE_TIMEOUT_SECONDS = 60 * 60
REVIEW_TIMEOUT_SECONDS = 45 * 60


def _repo_root() -> Path:
    """The repository root, not the directory the command happened to run in.

    ``git ls-files`` lists relative to the current directory, so a CLI run
    from a subdirectory used to fingerprint only that subtree.
    """
    try:
        return repo_toplevel(Path.cwd())
    except StateError:
        return Path.cwd()


# ------------------------------------------------------------------- locks


def acquire_run_lock(run_dir: Path, *, purpose: str, args: argparse.Namespace | None = None):
    """Take the lock for one run. Raises :class:`LockBusy` when held."""
    lock = FileLock(
        run_lock_path(run_dir), purpose=purpose, run_id=Path(run_dir).name
    )
    lock.acquire(
        wait_seconds=float(getattr(args, "lock_wait", 0.0) or 0.0),
        break_stale=bool(getattr(args, "break_lock", False)),
    )
    return lock


def _lock_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--lock-wait",
        type=float,
        default=0.0,
        help="seconds to wait for the run lock before reporting who holds it",
    )
    parser.add_argument(
        "--break-lock",
        action="store_true",
        help="delete a lock left behind by a crashed session. The break is "
        "recorded in the run. It does not touch the holder process.",
    )


def _open(
    args: argparse.Namespace, purpose: str, *, need_brief: bool = True
) -> tuple[Path, FileLock, RunState, Brief | None]:
    # Resolved: every artefact path is built from this, and a relative one
    # would be recorded relative to whatever directory the command ran in.
    run_dir = Path(args.run_dir).resolve()
    lock = acquire_run_lock(run_dir, purpose=purpose, args=args)
    try:
        state = load_state(run_dir)
        brief = _approved_brief(run_dir, state) if need_brief else None
        if getattr(args, "break_lock", False):
            state.events.append(
                {
                    "at": utc_now().isoformat(timespec="seconds"),
                    "event": "run lock broken",
                    "purpose": purpose,
                }
            )
            state.save(run_dir)
    except BaseException:
        lock.release()
        raise
    return run_dir, lock, state, brief


# ------------------------------------------------------------------ inputs


def _approved_brief(run_dir: Path, state: RunState) -> Brief:
    """The brief the run was approved with — the archived copy, never the
    source file.

    The source can be edited after approval (and, when it lives outside the
    repository, such an edit does not even change the tree digest), which
    would silently swap the verification commands or the allowed scope under
    a run that had already been approved. So: execute the archive, and refuse
    to continue when the source no longer matches it.
    """
    archived = run_dir / "brief.toml"
    if not archived.exists():
        raise BriefError(f"the run at {run_dir} has no archived brief")
    brief = load_brief(archived)
    if text_digest(brief.raw_text) != state.brief_digest:
        raise BriefError(
            "the archived brief does not match the digest recorded at start; "
            "the run directory has been tampered with"
        )
    source = Path(state.brief_path)
    if source.exists():
        current = source.read_text(encoding="utf-8")
        if text_digest(current) != state.brief_digest:
            raise BriefError(
                f"the brief at {source} changed after the run was approved; "
                "the approved inputs are no longer what that file says — start "
                "a new run rather than continuing under edited requirements"
            )
    return brief


def _run_dir_for(brief: Brief, root: Path, now: datetime) -> Path:
    slug = "".join(c if c.isalnum() or c in "-_" else "-" for c in brief.name).strip("-")
    return root / f"{now:%Y%m%d-%H%M%S}-{slug[:48] or 'run'}"


def _terminal_block(state: RunState) -> str | None:
    """A closed run stays closed.

    Stopping is how the workflow records "this needs the founder". If a later
    command could reopen it — or, worse, convert it into a delivery using an
    approval that predates the stop — the stop would be advisory rather than
    an outcome.
    """
    if state.status == "stopped":
        return (
            f"this run was stopped ({state.stop_reason or 'no reason recorded'}); "
            "start a new run rather than continuing a closed one"
        )
    if state.status == "delivered":
        return "this run was already delivered; start a new run for further work"
    return None


def _budget_block(state: RunState) -> str | None:
    """The two hard limits, checked before anything expensive starts."""
    if state.seconds_left() <= 0:
        return (
            f"total time budget exhausted ({state.max_total_seconds}s from "
            f"{state.started_at}); stop and report"
        )
    if state.auto_rounds_left <= 0 and state.review_count > 0:
        return (
            f"automatic round limit reached ({state.max_review_rounds} after the "
            f"initial review); stop and report"
        )
    return None


def _records(state: RunState) -> list[Finding]:
    return [Finding(**f) for f in state.findings]


def _joined(problems: list[str], preamble: str) -> str:
    return (
        preamble
        + "; ".join(problems[:6])
        + (f" (+{len(problems) - 6} more)" if len(problems) > 6 else "")
    )


def _evidence_block(state: RunState, run_dir: Path) -> str:
    """Whether every artefact whose digest was recorded is still itself.

    The run directory is excluded from the tree fingerprint — it has to be,
    or writing a gate log would invalidate the approval that log supports —
    which means deleting or rewriting the evidence moved nothing any other
    check looks at. Each file's digest is recorded when it is written, and a
    file that no longer matches is reported wherever it appears.

    A record with NO recorded digest is not an error here: it is simply
    unverifiable, and ``_support_block`` is what refuses to let anything
    unverifiable carry a delivery.
    """
    problems: list[str] = []
    for record in state.rounds:
        for channel, path in record.raw_paths.items():
            expected = (record.raw_digests or {}).get(channel)
            if not expected:
                continue
            actual = file_digest(artefact_path(run_dir, path))
            if not actual:
                problems.append(f"review {record.number} [{channel}] is missing: {path}")
            elif actual != expected:
                problems.append(f"review {record.number} [{channel}] was modified: {path}")
    for index, gate in enumerate(state.gates, start=1):
        for command in gate.get("commands", []):
            path, expected = command.get("log"), command.get("log_sha256")
            if not path or not expected:
                continue
            actual = file_digest(artefact_path(run_dir, path))
            if not actual:
                problems.append(f"gate {index} log is missing: {path}")
            elif actual != expected:
                problems.append(f"gate {index} log was modified: {path}")
    if not problems:
        return ""
    return _joined(
        problems,
        "the archived evidence no longer matches what was recorded, so the "
        "run cannot show what it was judged on: ",
    )


def _verifiable(problems: list[str], label: str, path: str, expected: str, run_dir: Path) -> None:
    if not path:
        problems.append(f"{label} archived nothing")
        return
    if not expected:
        problems.append(
            f"{label} has no recorded digest, so its archive cannot be shown to be "
            f"what was produced at the time: {path}"
        )
        return
    actual = file_digest(artefact_path(run_dir, path))
    if not actual:
        problems.append(f"{label} is missing: {path}")
    elif actual != expected:
        problems.append(f"{label} was modified: {path}")


def _support_block(state: RunState, record: RoundRecord, run_dir: Path) -> str:
    """Whether the evidence a DELIVERY would rest on is complete and checkable.

    Unverifiable is not the same as verified. A record whose digest was never
    taken cannot show that its archive is the output that was produced then,
    so it may not be what a delivery rests on — and back-filling a digest now
    would only prove the file has not changed since this moment, which is not
    the claim being made. The way forward is a fresh gate and review under
    the current version, whose evidence is verifiable by construction.

    Older runs stay readable and keep their history; what they lose is the
    ability to deliver on evidence nobody can check.
    """
    problems: list[str] = []
    for channel in REVIEW_CHANNELS:
        _verifiable(
            problems,
            f"the approving review {record.number} [{channel}]",
            record.raw_paths.get(channel, ""),
            (record.raw_digests or {}).get(channel, ""),
            run_dir,
        )
    gate = state.gates[-1] if state.gates else None
    if gate is None:
        problems.append("no verification run was recorded")
    else:
        for command in gate.get("commands", []):
            _verifiable(
                problems,
                "the supporting verification log",
                command.get("log") or "",
                command.get("log_sha256") or "",
                run_dir,
            )
    if not problems:
        return ""
    return _joined(
        problems,
        "the evidence this delivery would rest on is not verifiable, so it "
        "cannot show what it was judged on — re-run 'gate' and 'review' on "
        "this head rather than back-filling anything: ",
    )


def _release_block(state: RunState) -> str | None:
    """Why this run may not be delivered, beyond its verdict.

    An approve on one channel does not release a blocking finding raised on
    the other: the channels disagree in practice, and a workflow that let
    either one clear the other's findings would systematically lose the
    findings only one channel ever sees.
    """
    blockers = unresolved_blocking(_records(state))
    if blockers:
        lines = "; ".join(f.one_line() for f in blockers)
        return (
            f"{len(blockers)} blocking finding(s) are unresolved, so no channel's "
            f"approval releases this run: {lines}"
        )
    missing = untriaged_channels(
        rounds=state.rounds,
        findings=_records(state),
        attestations=state.triage,
        channels=REVIEW_CHANNELS,
    )
    if missing:
        return (
            "these review channels are unaccounted for: "
            + ", ".join(missing)
            + " — record their findings, or attest what you read ('findings "
            "none'); an unread channel is not a clean one, and a channel whose "
            "outcome is unknown is not an empty one"
        )
    return None


# --------------------------------------------------------------- commands


def cmd_start(args: argparse.Namespace) -> int:
    try:
        brief = load_brief(args.brief)
    except BriefError as exc:
        _emit(f"MISUSE: {exc}")
        return EXIT_MISUSE

    repo_root = _repo_root()
    now = utc_now()
    run_dir = (
        Path(args.run_dir)
        if args.run_dir
        else _run_dir_for(brief, Path(args.runs_root), now)
    ).resolve()
    # The lock is taken before the "already exists" check, so two starts
    # racing on one directory cannot both pass it and then overwrite each
    # other's state.
    run_dir.mkdir(parents=True, exist_ok=True)
    try:
        lock = acquire_run_lock(run_dir, purpose="start", args=args)
    except LockBusy as exc:
        _emit(f"BLOCKED: another command is operating this run — {exc}")
        return EXIT_BLOCKED
    try:
        return _start_locked(args, brief, repo_root, run_dir, now)
    finally:
        lock.release()


def _start_locked(
    args: argparse.Namespace,
    brief: Brief,
    repo_root: Path,
    run_dir: Path,
    now: datetime,
) -> int:
    try:
        # A base is stored as a COMMIT, never as a name: "HEAD" or a branch
        # moves under the run, and the recorded base would still look right
        # while the review silently shrank to the newest commits.
        base = resolve_commit(repo_root, args.base) if args.base else head_sha(repo_root)
    except StateError as exc:
        _emit(f"MISUSE: {exc}")
        return EXIT_MISUSE

    rounds = min(args.max_review_rounds, brief.limits.max_review_rounds)
    seconds = min(args.max_total_seconds, brief.limits.max_total_seconds)
    if (run_dir / "run.json").exists():
        _emit(f"MISUSE: a run already exists at {run_dir}; resume it instead of restarting")
        return EXIT_MISUSE
    # A run directory that already holds part of the repository would be
    # excluded from the tree fingerprint, hiding that source from every
    # staleness check for the life of the run.
    if run_dir.exists() and _run_dir_inside_repo(repo_root, run_dir):
        try:
            tracked = git_output(
                repo_root, "ls-files", "--error-unmatch", "--", str(run_dir)
            )
        except StateError:
            tracked = ""
        if tracked.strip():
            _emit(
                f"MISUSE: {run_dir} contains files tracked by this repository. The "
                "run directory is excluded from the tree fingerprint, so using it "
                "would hide that source from every staleness check; choose an "
                "empty directory"
            )
            return EXIT_MISUSE

    # The review base is pinned once, here, and never rebound. Re-basing each
    # round onto the previous round's head silently shrinks the review to the
    # last few commits, which is how a package can reach the end without
    # anything having examined it as a whole.
    unchecked_base = ""
    expected = merge_base(repo_root, args.integration_ref)
    if expected is None:
        # A missing or misspelled reference must not silently leave the base
        # at HEAD: the review would then cover nothing the package added.
        if not args.base:
            _emit(
                f"MISUSE: cannot resolve --integration-ref {args.integration_ref!r}, "
                "so the merge base is unknown; fetch it, or pass an explicit "
                "--base that you have checked covers the whole package"
            )
            return EXIT_MISUSE
        unchecked_base = (
            f"{args.integration_ref!r} could not be resolved, so the explicit "
            f"--base {base} was accepted WITHOUT being checked against the "
            "merge base"
        )
        _emit(f"NOTE: {unchecked_base}")
    else:
        if not args.base:
            base = expected
        if base != expected:
            _emit(
                f"MISUSE: --base {base} is not the merge base of HEAD and "
                f"{args.integration_ref} ({expected}); a review bound to a later "
                "commit would not see the whole package"
            )
            return EXIT_MISUSE
    if not is_ancestor(repo_root, base):
        _emit(f"MISUSE: base {base} is not an ancestor of HEAD")
        return EXIT_MISUSE

    linked_run = ""
    if args.linked_run:
        # Resolved and READ here. Stored as typed, a relative link resolved
        # against whatever directory a later command ran in, the load failed,
        # and the delivery reported no carried items at all - which is
        # indistinguishable from the older run having none.
        linked = Path(args.linked_run).resolve()
        try:
            load_state(linked)
        except StateError as exc:
            _emit(
                f"MISUSE: --linked-run {args.linked_run} cannot be read as a run "
                f"({exc}); a link whose state cannot be loaded would silently "
                "carry nothing forward"
            )
            return EXIT_MISUSE
        linked_run = str(linked)

    state = new_state(
        run_id=run_dir.name,
        brief_name=brief.name,
        brief_path=Path(brief.source_path or args.brief).resolve(),
        brief_digest=text_digest(brief.raw_text),
        repo_root=repo_root,
        base=base,
        max_review_rounds=rounds,
        max_total_seconds=seconds,
        linked_run=linked_run,
        now=now,
    )
    if unchecked_base:
        # Recorded, not just printed: a later reader of the run has no access
        # to what scrolled past in a terminal.
        state.events.append(
            {
                "at": utc_now().isoformat(timespec="seconds"),
                "event": "base accepted without a merge-base check",
                "detail": unchecked_base,
            }
        )
    state.save(run_dir)
    # The brief is archived verbatim: the run must be auditable even if the
    # source file is later edited.
    (run_dir / "brief.toml").write_text(brief.raw_text, encoding="utf-8")
    (run_dir / "brief.json").write_text(
        json.dumps(brief.to_public_dict(), indent=2) + "\n", encoding="utf-8"
    )
    _emit(f"run started: {run_dir}")
    _emit(f"base: {base}")
    if state.linked_run:
        _emit(f"continues: {state.linked_run} (that run keeps its own counts and history)")
    _emit(
        f"limits: {rounds} automatic round(s) after the initial review, "
        f"{seconds}s total, deadline {state.deadline_at}"
    )
    _emit(f"allowed paths: {', '.join(brief.allowed_paths)}")
    if _run_dir_inside_repo(repo_root, run_dir):
        _emit(
            "NOTE: the run directory is inside the repository; its artefacts are "
            "excluded from the tree fingerprint so that writing a log does not "
            "invalidate the approval it supports"
        )
    return EXIT_OK

def _run_dir_inside_repo(repo_root: Path, run_dir: Path) -> bool:
    try:
        Path(run_dir).resolve().relative_to(Path(repo_root).resolve())
    except ValueError:
        return False
    return True


def cmd_gate(args: argparse.Namespace) -> int:
    try:
        run_dir, lock, state, brief = _open(args, "gate")
    except StateError as exc:
        _emit(f"MISUSE: {exc}")
        return EXIT_MISUSE
    except BriefError as exc:
        _emit(f"BLOCKED: {exc}")
        return EXIT_BLOCKED
    except LockBusy as exc:
        _emit(f"BLOCKED: another command is operating this run — {exc}")
        return EXIT_BLOCKED
    try:
        return _gate(args, run_dir, state, brief)
    finally:
        lock.release()


def _gate(
    args: argparse.Namespace, run_dir: Path, state: RunState, brief: Brief
) -> int:
    blocked = _terminal_block(state) or _budget_block(state)
    if blocked:
        _emit(f"BLOCKED: {blocked}")
        return EXIT_BLOCKED

    # Runs in different worktrees have different run directories, so the run
    # lock does not keep them off the one PostgreSQL instance these suites
    # share. Concurrency there corrupts the schema for both. This lock is
    # machine-wide and named by the brief; a second run QUEUES behind it.
    shared_name = args.shared_lock or brief.limits.shared_lock
    shared = FileLock(
        shared_lock_path(shared_name), purpose="gate", run_id=state.run_id
    )
    wait = min(args.shared_lock_wait, max(0.0, state.seconds_left()))
    if args.break_shared_lock:
        # Machine-wide exclusion is the thing protecting every OTHER run's
        # database, so breaking it is recorded in this run before it happens.
        state.events.append(
            {
                "at": utc_now().isoformat(timespec="seconds"),
                "event": "shared gate lock broken",
                "detail": f"lock {shared_name!r}; other runs were not stopped",
            }
        )
        state.save(run_dir)
    try:
        shared.acquire(wait_seconds=wait, break_stale=bool(args.break_shared_lock))
    except LockBusy as exc:
        _emit(
            f"BLOCKED: the verification suites of this repository share one "
            f"database, and that resource is in use — {exc}. Waiting is the "
            "correct response; nothing else is stopped."
        )
        return EXIT_BLOCKED
    try:
        return _gate_locked(args, run_dir, state, brief)
    finally:
        shared.release()


def _gate_locked(
    args: argparse.Namespace, run_dir: Path, state: RunState, brief: Brief
) -> int:
    repo_root = Path(state.repo_root)
    exclude = (run_dir,)
    # Captured BEFORE the suite runs: a file edited after its own tests
    # passed but before the suite finished would otherwise have those passes
    # recorded against the edited tree.
    head_before = head_sha(repo_root)
    digest_before = tree_digest(repo_root, exclude)

    # One directory per ATTEMPT, not per review: numbering by review_count
    # made a re-run before the next review overwrite the previous attempt's
    # logs, so a failed attempt could vanish while its record still pointed
    # at the replacement output. Observed in this workflow's own run.
    log_dir = run_dir / f"gate-{len(state.gates) + 1:02d}"
    # The attempt is recorded as INCOMPLETE and its directory reserved before
    # any command runs. Recording only on completion meant an interrupted
    # re-run left an older passing gate as the newest record — so a delivery
    # could sit on verification that never finished — and the retry reused
    # the interrupted attempt's directory, overwriting its logs.
    state.gates.append(
        {
            "at": utc_now().isoformat(timespec="seconds"),
            "head": head_before,
            "tree_digest": digest_before,
            "inputs_stable": True,
            "commands": [],
            "passed": False,
            "ran": 0,
            "of": len(brief.verification_commands),
            "note": "verification was started but never completed",
        }
    )
    state.save(run_dir)

    results = run_gate_commands(
        brief.verification_commands,
        cwd=repo_root,
        log_dir=log_dir,
        timeout_seconds=args.timeout,
        budget_seconds=state.seconds_left(),
    )
    head_after = head_sha(repo_root)
    digest_after = tree_digest(repo_root, exclude)
    unchanged = head_before == head_after and digest_before == digest_after
    record = {
        "at": utc_now().isoformat(timespec="seconds"),
        "head": head_before,
        "tree_digest": digest_before,
        "inputs_stable": unchanged,
        "commands": [
            {
                "argv": r.argv,
                "exit_code": r.exit_code,
                "timed_out": r.timed_out,
                "killed_tree": r.killed_tree,
                "log_sha256": r.log_digest,
                "duration_seconds": round(r.duration_seconds, 1),
                "log": str(r.log_path) if r.log_path else None,
            }
            for r in results
        ],
        "passed": bool(results) and all(r.ok for r in results) and unchanged,
        "ran": len(results),
        "of": len(brief.verification_commands),
    }
    state.gates[-1] = record
    state.save(run_dir)

    for result in results:
        status = "ok" if result.ok else ("TIMEOUT" if result.timed_out else "FAILED")
        _emit(f"{status:8} {' '.join(result.argv)}  -> {result.log_path}")
        if result.timed_out and result.killed_tree:
            _emit("         (its process tree was terminated; no workers left behind)")
    if not unchanged:
        _emit(
            "BLOCKED: the working tree changed while the verification commands "
            "were running, so their results describe a tree that no longer "
            "exists; re-run the gate on a settled tree"
        )
        return EXIT_BLOCKED
    if not record["passed"]:
        _emit(
            "BLOCKED: a required verification command did not pass; fix it before "
            "any review — a failing gate is never a pass"
        )
        return EXIT_BLOCKED
    _emit(f"gate passed: {record['ran']}/{record['of']} commands, logs in {log_dir}")
    return EXIT_OK


def _focus_text(state: RunState, brief: Brief, run_dir: Path, extra: str) -> str:
    """What the reviewer is told — see ``prompt`` for why it says that."""
    return review_instructions(
        package=brief.name,
        base=state.base,
        requirements=brief.requirements,
        acceptance_criteria=brief.acceptance_criteria,
        allowed_paths=brief.allowed_paths,
        prohibitions=brief.prohibitions,
        gate_logs=sorted(str(p) for p in run_dir.glob("gate-*/*.log")),
        run_dir=str(run_dir),
        extra=extra,
    )


def cmd_review(args: argparse.Namespace) -> int:
    try:
        run_dir, lock, state, brief = _open(args, "review")
    except StateError as exc:
        _emit(f"MISUSE: {exc}")
        return EXIT_MISUSE
    except BriefError as exc:
        _emit(f"BLOCKED: {exc}")
        return EXIT_BLOCKED
    except LockBusy as exc:
        _emit(f"BLOCKED: another command is operating this run — {exc}")
        return EXIT_BLOCKED
    try:
        return _review(args, run_dir, state, brief)
    finally:
        lock.release()


def _review(args: argparse.Namespace, run_dir: Path, state: RunState, brief: Brief) -> int:
    blocked = _terminal_block(state) or _budget_block(state)
    if blocked:
        # A run that is ALREADY closed keeps the outcome it closed with. An
        # earlier version overwrote it, so a delivered run's own record could
        # end up saying it was stopped.
        if state.status == "open":
            state.status = "stopped"
            state.stop_reason = blocked
            state.save(run_dir)
        _emit(f"BLOCKED: {blocked}")
        return EXIT_BLOCKED

    repo_root = Path(state.repo_root)
    exclude = (run_dir,)
    last_gate = state.gates[-1] if state.gates else None
    current_head = head_sha(repo_root)
    current_digest = tree_digest(repo_root, exclude)
    if last_gate is None:
        _emit("BLOCKED: no verification run recorded; run 'gate' before 'review'")
        return EXIT_BLOCKED
    if last_gate["head"] != current_head or last_gate["tree_digest"] != current_digest:
        _emit(
            "BLOCKED: the working tree changed after the last verification run, so "
            "that evidence is stale; re-run 'gate' before reviewing"
        )
        return EXIT_BLOCKED
    if not last_gate["passed"]:
        _emit("BLOCKED: the last verification run did not pass")
        return EXIT_BLOCKED

    extra = ""
    if args.focus_file:
        try:
            extra = Path(args.focus_file).read_text(encoding="utf-8").strip()
        except OSError as exc:
            _emit(f"MISUSE: cannot read focus file: {exc}")
            return EXIT_MISUSE

    if not is_ancestor(repo_root, state.base):
        _emit(
            f"BLOCKED: the recorded base {state.base} is no longer an ancestor of "
            "HEAD (rebase or reset?); a review bound to it would not describe "
            "this branch"
        )
        return EXIT_BLOCKED

    # The reviewer is handed a COMMIT RANGE. The plugin resolves an explicit
    # base to branch mode before it considers the scope, and collects
    # `git diff <base>..HEAD`, so uncommitted work is invisible to it — while
    # the tree digest would happily bind the resulting approval to that
    # uncommitted work. Reviewing a dirty tree therefore produces an approval
    # describing code nobody read.
    missing_evidence = _evidence_block(state, run_dir)
    if missing_evidence:
        _emit(f"BLOCKED: {missing_evidence}")
        return EXIT_BLOCKED

    dirty = dirty_paths(repo_root, exclude)
    if dirty:
        shown = ", ".join(dirty[:8]) + (f" (+{len(dirty) - 8} more)" if len(dirty) > 8 else "")
        _emit(
            "BLOCKED: the working tree has uncommitted changes, and the reviewer "
            f"only reads the commits {state.base}..HEAD — it would never see "
            f"them: {shown}. Commit them, then re-run 'gate' and 'review'."
        )
        return EXIT_BLOCKED

    number = state.review_count + 1
    kind = "initial" if number == 1 else "auto"
    raw_paths = {
        channel: run_dir / f"review-{number:02d}-{channel}.log"
        for channel in REVIEW_CHANNELS
    }
    started = utc_now()
    # The attempt is counted and its evidence filenames claimed BEFORE the
    # reviewer is called. A session that dies between the two channels would
    # otherwise resume with the old counter — buying back a spent round and
    # overwriting the interrupted attempt's raw output.
    state.rounds.append(
        RoundRecord(
            number=number,
            kind=kind,
            started_at=started.isoformat(timespec="seconds"),
            finished_at="",
            base=state.base,
            head=current_head,
            tree_digest=current_digest,
            verdict="unusable",
            usable=False,
            reason="the review was started but never completed",
            raw_paths={c: str(p) for c, p in raw_paths.items()},
            exit_codes={},
            duration_seconds=0.0,
            channel_status={c: "pending" for c in REVIEW_CHANNELS},
        )
    )
    state.save(run_dir)

    def persist_channel(channel: str, result) -> None:
        """Record one channel's outcome BEFORE the next one starts.

        An interruption between channels used to leave the first channel's
        archive on disk with nothing saying it had run: its exit code, its
        digest and therefore its findings were lost, and the round read as
        having nothing to account for.
        """
        record = state.rounds[-1]
        record.exit_codes[channel] = result.exit_code
        record.raw_digests[channel] = result.log_digest
        record.channel_status[channel] = "completed" if result.exit_code == 0 else "failed"
        state.save(run_dir)

    # Never let a review outlive the run's own deadline.
    timeout = min(args.timeout, max(1.0, state.seconds_left()))
    results = invoke_review_round(
        base=state.base,
        focus=_focus_text(state, brief, run_dir, extra),
        cwd=repo_root,
        raw_paths=raw_paths,
        timeout_seconds=timeout,
        scope=args.scope,
        on_channel_complete=persist_channel,
    )
    gating, prose = results[CHANNEL_VERDICT], results[CHANNEL_FINDINGS]
    gating_read = read_channel(
        payload=gating.payload(),
        payload_mode=gating.payload_mode(),
        raw_text=gating.combined,
        exit_code=gating.exit_code,
        timed_out=gating.timed_out,
        expects_verdict=True,
    )
    prose_read = read_channel(
        payload=prose.payload(),
        payload_mode=prose.payload_mode(),
        raw_text=prose.combined,
        exit_code=prose.exit_code,
        timed_out=prose.timed_out,
        expects_verdict=False,
    )
    verdict = combine_round(
        gating_read.verdict, prose_read.ok, prose_read.reason, prose_read.review_text
    )

    recorded: list[Finding] = []
    findings_unreadable = ""
    if gating_read.structured is not None:
        known = {f["id"] for f in state.findings}
        try:
            recorded = findings_from_structured(
                gating_read.structured,
                round_number=number,
                channel=CHANNEL_VERDICT,
                recorded_at=utc_now().isoformat(timespec="seconds"),
                existing=known,
            )
        except FindingsError as exc:
            # The reviewer promised structured findings and returned something
            # that cannot be read. Dropping them silently is exactly how a
            # blocking defect would disappear between reviewer and release.
            findings_unreadable = str(exc)
            verdict = type(verdict)(
                "unusable", False, f"the reviewer's findings could not be read: {exc}"
            )
        if not findings_unreadable:
            for finding in recorded:
                if OUT_OF_SCOPE_MARKER in finding.title.upper():
                    finding.out_of_scope = True
                    finding.disposition = AWAITING
                    finding.note = "reported as necessary but outside the approved scope"
            state.findings.extend(f.to_dict() for f in recorded if f.id not in known)
            # A structured result IS the triage of that channel: its findings
            # were read mechanically, including when there are none of them.
            # Only on SUCCESS: attesting "read, 0 findings" after a parse
            # failure both lies about what was read and locks the agent out of
            # recording the findings by hand, since a channel cannot be both
            # attested clean and carry findings.
            state.triage.append(
                {
                    "round": number,
                    "channel": CHANNEL_VERDICT,
                    "at": utc_now().isoformat(timespec="seconds"),
                    "note": f"read from the plugin's structured result "
                    f"({len(recorded)} finding(s))",
                }
            )

    state.rounds[-1] = RoundRecord(
        number=number,
        kind=kind,
        started_at=started.isoformat(timespec="seconds"),
        finished_at=utc_now().isoformat(timespec="seconds"),
        base=state.base,
        head=current_head,
        tree_digest=current_digest,
        verdict=verdict.value,
        usable=verdict.usable,
        reason=verdict.reason,
        raw_paths={c: str(p) for c, p in raw_paths.items()},
        raw_digests={c: r.log_digest for c, r in results.items()},
        exit_codes={c: r.exit_code for c, r in results.items()},
        channel_status={
            c: ("completed" if r.exit_code == 0 else "failed") for c, r in results.items()
        },
        duration_seconds=round(sum(r.duration_seconds for r in results.values()), 1),
    )
    state.save(run_dir)

    _emit(f"review {number} ({kind}): {verdict.value} — {verdict.reason}")
    for channel, path in raw_paths.items():
        _emit(f"raw output [{channel}]: {path}")
    for finding in recorded:
        _emit(f"  {finding.one_line()}")
    if findings_unreadable:
        _emit(
            f"NOTE: the reviewer's structured findings could not be read "
            f"({findings_unreadable}); none were recorded automatically and this "
            "channel is NOT attested — read the raw file and record them yourself"
        )
    elif gating_read.structured is None:
        _emit(
            "NOTE: the reviewer returned no structured result, so no findings "
            "were read mechanically; record what you read yourself"
        )
    _emit(
        f"read BOTH channels. {CHANNEL_VERDICT} returns a structured result and its "
        f"findings are recorded above; {CHANNEL_FINDINGS} returns prose — read it and "
        f"record its findings with 'findings record', or attest 'findings none'"
    )
    _emit(
        f"automatic rounds left after this: {state.auto_rounds_left}; "
        f"time left: {int(state.seconds_left())}s"
    )
    out_of_scope = [f for f in recorded if f.out_of_scope]
    if out_of_scope:
        _emit(
            "STOP AND ASK: the reviewer reported necessary work outside the "
            "approved scope. It is recorded as awaiting adjudication and must "
            "not be implemented by this run."
        )
    if not verdict.usable:
        _emit(
            "BLOCKED: no usable review result. This does not count as a pass. "
            "Re-run once if the cause was transient, otherwise stop and report."
        )
        return EXIT_BLOCKED
    if verdict.value != "approve":
        _emit("read the raw output, then fix real in-scope defects or refute them with evidence")
        return EXIT_OK
    release = _release_block(state)
    if release:
        _emit(f"NOTE: the reviewer approved this head, but delivery is blocked — {release}")
        return EXIT_OK
    _emit("reviewer approved this head")
    return EXIT_OK


def cmd_status(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir).resolve()
    try:
        state = load_state(run_dir)
    except StateError as exc:
        _emit(f"MISUSE: {exc}")
        return EXIT_MISUSE
    repo_root = Path(state.repo_root)
    exclude = (run_dir,)
    passing = state.passing_round(repo_root, exclude)
    stale = (
        state.last_round() is not None
        and state.last_round().verdict == "approve"
        and state.last_round().usable
        and passing is None
    )
    blockers = unresolved_blocking(_records(state))
    payload = {
        "run_id": state.run_id,
        "linked_run": state.linked_run,
        "status": state.status,
        "stop_reason": state.stop_reason,
        "base": state.base,
        "head": head_sha(repo_root),
        "reviews_done": state.review_count,
        "auto_rounds_left": state.auto_rounds_left,
        "seconds_left": int(state.seconds_left()),
        "last_verdict": state.last_round().verdict if state.last_round() else None,
        "has_current_pass": passing is not None,
        "pass_is_stale": stale,
        "findings_recorded": len(state.findings),
        "findings_blocking_unresolved": len(blockers),
        "release_block": _release_block(state) or "",
        "evidence_block": _evidence_block(state, run_dir) or "",
        "gates_run": len(state.gates),
        "last_gate_passed": bool(state.gates and state.gates[-1]["passed"]),
    }
    if args.json:
        _emit(json.dumps(payload, indent=2))
    else:
        for key, value in payload.items():
            _emit(f"{key}: {value}")
    if stale:
        _emit("NOTE: the approval describes an older tree and no longer applies")
    for finding in blockers:
        _emit(f"BLOCKING: {finding.one_line()}")
    return EXIT_OK


def cmd_finish(args: argparse.Namespace) -> int:
    try:
        run_dir, lock, state, _ = _open(args, "finish", need_brief=False)
    except StateError as exc:
        _emit(f"MISUSE: {exc}")
        return EXIT_MISUSE
    except LockBusy as exc:
        _emit(f"BLOCKED: another command is operating this run — {exc}")
        return EXIT_BLOCKED
    try:
        return _finish(args, run_dir, state)
    finally:
        lock.release()


def _finish(args: argparse.Namespace, run_dir: Path, state: RunState) -> int:
    stopping = getattr(args, "stopping", False)
    terminal = _terminal_block(state)
    if terminal and not stopping:
        _emit(f"BLOCKED: {terminal}")
        return EXIT_BLOCKED

    repo_root = Path(state.repo_root)
    exclude = (run_dir,)
    passing = state.passing_round(repo_root, exclude)
    blocking_reason = ""
    if passing is not None and not stopping:
        # The same input check gate and review make: an approval means
        # nothing if the requirements it was judged against have since
        # changed, and a brief living outside the repository can change
        # without moving the tree digest at all.
        try:
            _approved_brief(run_dir, state)
        except BriefError as exc:
            blocking_reason = str(exc)
            passing = None
    if passing is not None:
        # An approval is not enough on its own: the verification run behind it
        # must still be the newest one and must still have passed, the
        # approval itself must have landed inside the run's time budget, and
        # no blocking finding from EITHER channel may still be open.
        release = (
            _release_block(state)
            or _evidence_block(state, run_dir)
            or _support_block(state, passing, run_dir)
            or _linked_block(state)
        )
        if not state.gate_supports(passing):
            blocking_reason = (
                "the newest verification run did not pass on the reviewed inputs; "
                "a delivery may not sit on top of a failing gate"
            )
        elif datetime.fromisoformat(passing.finished_at) > datetime.fromisoformat(
            state.deadline_at
        ):
            blocking_reason = (
                "the approval was produced after the run's total time budget "
                "expired; stop and report instead of delivering"
            )
        elif release:
            blocking_reason = release
        if blocking_reason:
            passing = None

    if passing is None and not args.force:
        last = state.last_round()
        if blocking_reason:
            reason = blocking_reason
        elif last is None:
            reason = "no review was performed"
        elif not last.usable:
            reason = f"the last review was unusable ({last.reason})"
        elif last.verdict != "approve":
            reason = f"the last review reported {last.verdict}"
        else:
            reason = "the approval describes an older tree (head or working tree moved)"
        _emit(f"BLOCKED: cannot finish as reviewed — {reason}")
        _emit("use --force only to close a run that is stopping WITHOUT a pass, and say so")
        return EXIT_BLOCKED

    state.status = "delivered" if passing is not None and not stopping else "stopped"
    if state.status == "stopped":
        state.stop_reason = args.reason or "closed without a passing review"
    state.save(run_dir)

    # Recorded so the cost of the workflow is measurable from the first run
    # rather than argued about: wall clock from approval to delivery, and the
    # time actually spent inside gates and reviews.
    review_seconds = sum(r.duration_seconds for r in state.rounds)
    gate_seconds = sum(
        c.get("duration_seconds", 0) for g in state.gates for c in g["commands"]
    )
    summary = {
        "run_id": state.run_id,
        "linked_run": state.linked_run,
        "brief": state.brief_name,
        "base": state.base,
        "wall_clock_seconds": int(
            (utc_now() - datetime.fromisoformat(state.started_at)).total_seconds()
        ),
        "review_seconds": round(review_seconds, 1),
        "gate_seconds": round(gate_seconds, 1),
        "archived_review_artefacts": sum(len(r.raw_paths) for r in state.rounds),
        "expected_review_artefacts": state.review_count * len(REVIEW_CHANNELS),
        "head": head_sha(repo_root),
        "status": state.status,
        "stop_reason": state.stop_reason,
        "reviews": [
            {
                "number": r.number,
                "kind": r.kind,
                "verdict": r.verdict,
                "usable": r.usable,
                "reason": r.reason,
                "head": r.head,
                "raw": r.raw_paths,
            }
            for r in state.rounds
        ],
        "findings": state.findings,
        "triage": state.triage,
        "unresolved_blocking": [f.id for f in unresolved_blocking(_records(state))],
        # Read-only, and never merged into this run's own counts: a run that
        # continues another one has to carry that one's open items forward or
        # they vanish between records.
        "linked_unresolved": _linked_unresolved(state),
        "events": state.events,
        "gates": state.gates,
    }
    path = run_dir / "delivery.json"
    path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    _emit(f"{state.status}: evidence index written to {path}")
    _emit("this workflow never merges and never deploys; open a Draft PR for the founder")
    return EXIT_OK


def _linked_block(state: RunState) -> str:
    """Why a linked run stops this delivery.

    An unreadable link and a link with nothing open used to produce the same
    empty list, so losing the carried items looked exactly like having none.
    A link that cannot be read is now a refusal, not a silent zero.
    """
    if not state.linked_run:
        return ""
    try:
        load_state(Path(state.linked_run))
    except StateError as exc:
        return (
            f"the run this one continues ({state.linked_run}) cannot be read "
            f"({exc}), so its open items cannot be carried forward and their "
            "absence here would mean nothing"
        )
    return ""


def _linked_unresolved(state: RunState) -> list[dict]:
    """The open findings of the run this one continues.

    Read without modifying it: the older run keeps its status, its counts and
    its history exactly as they were. Callers check ``_linked_block`` first;
    an unreadable link is a refusal, never an empty list.
    """
    if not state.linked_run:
        return []
    try:
        older = load_state(Path(state.linked_run))
    except StateError:
        return []
    return [
        {
            "run_id": older.run_id,
            "id": f.id,
            "severity": f.severity,
            "title": f.title,
            "file": f.file,
            "disposition": f.disposition,
        }
        for f in _records(older)
        if not f.resolved
    ]


def cmd_stop(args: argparse.Namespace) -> int:
    """Close a run deliberately without a pass.

    Stopping is a first-class outcome, not a failure of the workflow: the
    most valuable thing the manual loop ever did was stop and escalate a
    defect that needed authorisation nobody had yet.

    It is also TERMINAL and unconditional. An earlier approval does not turn
    a stop into a delivery — a run stopped for a requirements conflict or for
    scope it was not granted must record exactly that, with its reason.
    """
    args.force = True
    args.stopping = True
    return cmd_finish(args)


# ------------------------------------------------------------------ parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.review_handoff",
        description="Development/review handoff for one approved work package.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    start = sub.add_parser("start", help="begin a run from an approved brief")
    start.add_argument("--brief", required=True)
    start.add_argument("--run-dir")
    start.add_argument("--runs-root", default=str(DEFAULT_RUNS_ROOT))
    start.add_argument("--base")
    start.add_argument("--max-review-rounds", type=int, default=10**6)
    start.add_argument("--max-total-seconds", type=int, default=10**9)
    start.add_argument(
        "--linked-run",
        default="",
        help="the run directory this one continues; the older run is not "
        "modified, its counts and history stay as they are",
    )
    start.add_argument(
        "--integration-ref",
        default="origin/main",
        help="the branch this package will merge into; the review base must be "
        "its merge base with HEAD so the review never shrinks to a slice",
    )
    _lock_arguments(start)
    start.set_defaults(func=cmd_start)

    gate = sub.add_parser("gate", help="run the brief's verification commands in order")
    gate.add_argument("--run-dir", required=True)
    gate.add_argument("--timeout", type=float, default=GATE_TIMEOUT_SECONDS)
    gate.add_argument(
        "--shared-lock",
        default="",
        help="name of the machine-wide lock these suites contend for "
        "(default: the brief's limits.shared_lock)",
    )
    gate.add_argument(
        "--shared-lock-wait", type=float, default=DEFAULT_GATE_LOCK_WAIT_SECONDS
    )
    gate.add_argument("--break-shared-lock", action="store_true")
    _lock_arguments(gate)
    gate.set_defaults(func=cmd_gate)

    review = sub.add_parser("review", help="hand the current head to the reviewer")
    review.add_argument("--run-dir", required=True)
    review.add_argument("--focus-file")
    review.add_argument("--scope", default="branch")
    review.add_argument("--timeout", type=float, default=REVIEW_TIMEOUT_SECONDS)
    _lock_arguments(review)
    review.set_defaults(func=cmd_review)

    add_findings_parser(sub, _lock_arguments)

    status = sub.add_parser("status", help="where the run stands")
    status.add_argument("--run-dir", required=True)
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=cmd_status)

    finish = sub.add_parser("finish", help="close the run and write the evidence index")
    finish.add_argument("--run-dir", required=True)
    finish.add_argument("--force", action="store_true")
    finish.add_argument("--reason", default="")
    _lock_arguments(finish)
    finish.set_defaults(func=cmd_finish, stopping=False)

    stop = sub.add_parser(
        "stop",
        help="end the run WITHOUT a pass (scope exceeded, conflict, limit reached)",
    )
    stop.add_argument("--run-dir", required=True)
    stop.add_argument("--reason", required=True)
    _lock_arguments(stop)
    stop.set_defaults(func=cmd_stop)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover - module entry point
    sys.exit(main())
