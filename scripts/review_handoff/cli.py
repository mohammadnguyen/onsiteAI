"""Command line for one handoff run.

    python -m scripts.review_handoff start   --brief <file> [--run-dir <dir>]
    python -m scripts.review_handoff gate    --run-dir <dir>
    python -m scripts.review_handoff review  --run-dir <dir> [--focus-file <file>]
    python -m scripts.review_handoff status  --run-dir <dir> [--json]
    python -m scripts.review_handoff finish  --run-dir <dir> [--force]

Exit codes are the contract the skill branches on:

    0  proceed — the step did what it says
    1  blocked — a limit, a stale verdict, a failing gate, an unusable review
    2  misuse  — bad arguments, missing or invalid brief, unreadable state

"blocked" is never an error to route around. It is the workflow stopping on
purpose, and the reason is printed and recorded.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from .brief import Brief, BriefError, load_brief
from .runner import (
    REVIEW_CHANNELS,
    invoke_review_round,
    run_gate_commands,
)
from .state import (
    RoundRecord,
    RunState,
    StateError,
    head_sha,
    is_ancestor,
    load_state,
    merge_base,
    new_state,
    text_digest,
    tree_digest,
    utc_now,
)
from .verdict import combine_round, parse_verdict

EXIT_OK = 0
EXIT_BLOCKED = 1
EXIT_MISUSE = 2

DEFAULT_RUNS_ROOT = Path(".claude/handoff")
GATE_TIMEOUT_SECONDS = 60 * 60
REVIEW_TIMEOUT_SECONDS = 45 * 60


def _repo_root() -> Path:
    return Path.cwd()


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


def _emit(message: str) -> None:
    print(message, flush=True)


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


# --------------------------------------------------------------- commands


def cmd_start(args: argparse.Namespace) -> int:
    try:
        brief = load_brief(args.brief)
    except BriefError as exc:
        _emit(f"MISUSE: {exc}")
        return EXIT_MISUSE

    repo_root = _repo_root()
    try:
        base = args.base or head_sha(repo_root)
    except StateError as exc:
        _emit(f"MISUSE: {exc}")
        return EXIT_MISUSE

    rounds = min(args.max_review_rounds, brief.limits.max_review_rounds)
    seconds = min(args.max_total_seconds, brief.limits.max_total_seconds)
    now = utc_now()
    run_dir = Path(args.run_dir) if args.run_dir else _run_dir_for(
        brief, Path(args.runs_root), now
    )
    if (run_dir / "run.json").exists():
        _emit(f"MISUSE: a run already exists at {run_dir}; resume it instead of restarting")
        return EXIT_MISUSE

    # The review base is pinned once, here, and never rebound. Re-basing each
    # round onto the previous round's head silently shrinks the review to the
    # last few commits, which is how a package can reach the end without
    # anything having examined it as a whole.
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
        _emit(
            f"NOTE: {args.integration_ref!r} could not be resolved; using the "
            f"explicit --base {base} unchecked against it"
        )
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

    state = new_state(
        run_id=run_dir.name,
        brief_name=brief.name,
        brief_path=Path(brief.source_path or args.brief).resolve(),
        brief_digest=text_digest(brief.raw_text),
        repo_root=repo_root,
        base=base,
        max_review_rounds=rounds,
        max_total_seconds=seconds,
        now=now,
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
    _emit(
        f"limits: {rounds} automatic round(s) after the initial review, "
        f"{seconds}s total, deadline {state.deadline_at}"
    )
    _emit(f"allowed paths: {', '.join(brief.allowed_paths)}")
    return EXIT_OK


def cmd_gate(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir)
    try:
        state = load_state(run_dir)
        brief = _approved_brief(run_dir, state)
    except StateError as exc:
        _emit(f"MISUSE: {exc}")
        return EXIT_MISUSE
    except BriefError as exc:
        _emit(f"BLOCKED: {exc}")
        return EXIT_BLOCKED

    blocked = _terminal_block(state) or _budget_block(state)
    if blocked:
        _emit(f"BLOCKED: {blocked}")
        return EXIT_BLOCKED

    # Captured BEFORE the suite runs: a file edited after its own tests
    # passed but before the suite finished would otherwise have those passes
    # recorded against the edited tree.
    head_before = head_sha(Path(state.repo_root))
    digest_before = tree_digest(Path(state.repo_root))

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
        cwd=Path(state.repo_root),
        log_dir=log_dir,
        timeout_seconds=args.timeout,
        budget_seconds=state.seconds_left(),
    )
    head_after = head_sha(Path(state.repo_root))
    digest_after = tree_digest(Path(state.repo_root))
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
    """What the reviewer is told: the requirements it must judge against, the
    approved scope, and the path to the RAW verification output for this very
    head — so the review reads the evidence itself rather than a claim about
    it."""
    gate_logs = sorted(str(p) for p in run_dir.glob("gate-*/*.log"))
    lines = [
        f"Work package: {brief.name}.",
        f"Review the diff {state.base}..HEAD plus any uncommitted changes in the "
        "working tree; that is the whole change under review.",
        "Approved requirements: " + " | ".join(brief.requirements),
        "Acceptance criteria: " + " | ".join(brief.acceptance_criteria),
        "Allowed change scope: " + " | ".join(brief.allowed_paths),
        "Prohibited: " + " | ".join(brief.prohibitions),
        "The verification commands in the brief were run against this exact head; "
        "their raw output is archived at: " + (", ".join(gate_logs) or str(run_dir))
        + ". Read it rather than trusting any summary of it.",
        "Report only grounded defects with file:line, a concrete scenario and a "
        "minimal in-scope fix. Do not propose work outside the allowed scope. "
        "End with a single line 'Verdict: approve' or 'Verdict: needs-attention'.",
    ]
    if extra:
        lines.append(extra)
    return " ".join(lines)


def cmd_review(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir)
    try:
        state = load_state(run_dir)
        brief = _approved_brief(run_dir, state)
    except StateError as exc:
        _emit(f"MISUSE: {exc}")
        return EXIT_MISUSE
    except BriefError as exc:
        _emit(f"BLOCKED: {exc}")
        return EXIT_BLOCKED

    blocked = _terminal_block(state) or _budget_block(state)
    if blocked:
        state.status = "stopped"
        state.stop_reason = blocked
        state.save(run_dir)
        _emit(f"BLOCKED: {blocked}")
        return EXIT_BLOCKED

    repo_root = Path(state.repo_root)
    last_gate = state.gates[-1] if state.gates else None
    current_head = head_sha(repo_root)
    current_digest = tree_digest(repo_root)
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
        )
    )
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
    )
    gating, findings = results["adversarial-review"], results["review"]
    verdict = combine_round(
        parse_verdict(
            gating.combined,
            exit_code=gating.exit_code,
            timed_out=gating.timed_out,
        ),
        findings.ok,
        "timed out" if findings.timed_out else f"exit {findings.exit_code}",
        findings.combined,
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
        exit_codes={c: r.exit_code for c, r in results.items()},
        duration_seconds=round(sum(r.duration_seconds for r in results.values()), 1),
    )
    state.save(run_dir)

    _emit(f"review {number} ({kind}): {verdict.value} — {verdict.reason}")
    for channel, path in raw_paths.items():
        _emit(f"raw output [{channel}]: {path}")
    _emit(
        "read BOTH channels: only the adversarial one carries a machine-readable "
        "verdict, the other reports findings in prose and is never classified here"
    )
    _emit(
        f"automatic rounds left after this: {state.auto_rounds_left}; "
        f"time left: {int(state.seconds_left())}s"
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
    _emit("reviewer approved this head")
    return EXIT_OK


def cmd_status(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir)
    try:
        state = load_state(run_dir)
    except StateError as exc:
        _emit(f"MISUSE: {exc}")
        return EXIT_MISUSE
    repo_root = Path(state.repo_root)
    passing = state.passing_round(repo_root)
    stale = (
        state.last_round() is not None
        and state.last_round().verdict == "approve"
        and state.last_round().usable
        and passing is None
    )
    payload = {
        "run_id": state.run_id,
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
    return EXIT_OK


def cmd_finish(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir)
    try:
        state = load_state(run_dir)
    except StateError as exc:
        _emit(f"MISUSE: {exc}")
        return EXIT_MISUSE

    stopping = getattr(args, "stopping", False)
    terminal = _terminal_block(state)
    if terminal and not stopping:
        _emit(f"BLOCKED: {terminal}")
        return EXIT_BLOCKED

    repo_root = Path(state.repo_root)
    passing = state.passing_round(repo_root)
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
        # must still be the newest one and must still have passed, and the
        # approval itself must have landed inside the run's time budget.
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
        "gates": state.gates,
    }
    path = run_dir / "delivery.json"
    path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    _emit(f"{state.status}: evidence index written to {path}")
    _emit("this workflow never merges and never deploys; open a Draft PR for the founder")
    return EXIT_OK


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
        "--integration-ref",
        default="origin/main",
        help="the branch this package will merge into; the review base must be "
        "its merge base with HEAD so the review never shrinks to a slice",
    )
    start.set_defaults(func=cmd_start)

    gate = sub.add_parser("gate", help="run the brief's verification commands in order")
    gate.add_argument("--run-dir", required=True)
    gate.add_argument("--timeout", type=float, default=GATE_TIMEOUT_SECONDS)
    gate.set_defaults(func=cmd_gate)

    review = sub.add_parser("review", help="hand the current head to the reviewer")
    review.add_argument("--run-dir", required=True)
    review.add_argument("--focus-file")
    review.add_argument("--scope", default="branch")
    review.add_argument("--timeout", type=float, default=REVIEW_TIMEOUT_SECONDS)
    review.set_defaults(func=cmd_review)

    status = sub.add_parser("status", help="where the run stands")
    status.add_argument("--run-dir", required=True)
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=cmd_status)

    finish = sub.add_parser("finish", help="close the run and write the evidence index")
    finish.add_argument("--run-dir", required=True)
    finish.add_argument("--force", action="store_true")
    finish.add_argument("--reason", default="")
    finish.set_defaults(func=cmd_finish, stopping=False)

    stop = sub.add_parser(
        "stop",
        help="end the run WITHOUT a pass (scope exceeded, conflict, limit reached)",
    )
    stop.add_argument("--run-dir", required=True)
    stop.add_argument("--reason", required=True)
    stop.set_defaults(func=cmd_stop)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover - module entry point
    sys.exit(main())
