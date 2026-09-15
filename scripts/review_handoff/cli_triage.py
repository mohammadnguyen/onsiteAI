"""The ``findings`` subcommands: recording what each finding turned into.

Judgement stays with the agent. Nothing in here scores reviewer prose or
decides whether a finding is real — it records a decision that was already
made, with a note saying on what basis, and then holds the run to it. The
structured channel's findings arrive on their own (the plugin returns them
against its own schema); these commands exist for the prose channel and for
resolving anything from either one.

Recording is mandatory in one direction only: a channel that produced a
review must end up with either findings or an explicit "none". Silence is
not evidence that nobody found anything.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .console import EXIT_BLOCKED, EXIT_MISUSE, EXIT_OK, emit
from .findings import (
    AWAITING,
    DISPOSITIONS,
    RESOLVED_DISPOSITIONS,
    SEVERITIES,
    SOURCE_AGENT,
    Finding,
    channel_state,
    finding_id,
)
from .locking import LockBusy
from .runner import REVIEW_CHANNELS
from .state import StateError, load_state, utc_now


def _load(args: argparse.Namespace):
    """Load a run under its lock; the caller must release it.

    Every failure releases. Catching only StateError left the lock held on
    anything else - a corrupt file, a permission error, a KeyboardInterrupt -
    and a run whose lock is held by a process that has exited needs a manual
    --break-lock to move again.
    """
    from .cli import acquire_run_lock  # local import: one-way dependency

    run_dir = Path(args.run_dir).resolve()
    lock = acquire_run_lock(run_dir, purpose="findings", args=args)
    try:
        state = load_state(run_dir)
    except BaseException:
        lock.release()
        raise
    if getattr(args, "break_lock", False):
        # Recorded here too: a break that leaves no trace is indistinguishable
        # from no contention having happened.
        state.events.append(
            {
                "at": utc_now().isoformat(timespec="seconds"),
                "event": "run lock broken",
                "purpose": "findings",
            }
        )
        state.save(run_dir)
    return run_dir, lock, state


def _round(state, number: int):
    for record in state.rounds:
        if record.number == number:
            return record
    return None


def cmd_findings_record(args: argparse.Namespace) -> int:
    try:
        run_dir, lock, state = _load(args)
    except StateError as exc:
        emit(f"MISUSE: {exc}")
        return EXIT_MISUSE
    except LockBusy as exc:
        emit(f"BLOCKED: {exc}")
        return EXIT_BLOCKED
    try:
        if args.channel not in REVIEW_CHANNELS:
            emit(f"MISUSE: unknown channel {args.channel!r}; expected one of {REVIEW_CHANNELS}")
            return EXIT_MISUSE
        record = _round(state, args.round)
        if record is None:
            emit(f"MISUSE: this run has no review round {args.round}")
            return EXIT_MISUSE
        attested = [
            a
            for a in state.triage
            if int(a["round"]) == args.round and a["channel"] == args.channel
        ]
        if attested:
            emit(
                f"MISUSE: round {args.round} channel {args.channel} was already "
                "attested as reporting no findings; that attestation and a "
                "finding cannot both be true - correct the record deliberately"
            )
            return EXIT_MISUSE

        new = Finding(
            id=finding_id(args.round, args.channel, args.title, args.file or "", args.line),
            round=args.round,
            channel=args.channel,
            severity=args.severity,
            title=args.title.strip(),
            file=(args.file or "").strip(),
            line_start=args.line,
            body=(args.body or "").strip(),
            recommendation=(args.recommendation or "").strip(),
            source=SOURCE_AGENT,
            out_of_scope=bool(args.out_of_scope),
            disposition=AWAITING if args.out_of_scope else "pending",
            note="reported as necessary but outside the approved scope"
            if args.out_of_scope
            else "",
            recorded_at=utc_now().isoformat(timespec="seconds"),
        )
        if any(f["id"] == new.id for f in state.findings):
            emit(f"MISUSE: finding {new.id} is already recorded")
            return EXIT_MISUSE
        state.findings.append(new.to_dict())
        state.save(run_dir)
        emit(f"recorded {new.one_line()}")
        if new.blocking:
            emit("this finding BLOCKS delivery until it is fixed or refuted")
        if new.out_of_scope:
            emit(
                "out of approved scope: do NOT change it. Stop the run and ask "
                "the founder for authorisation."
            )
        return EXIT_OK
    finally:
        lock.release()


def cmd_findings_none(args: argparse.Namespace) -> int:
    try:
        run_dir, lock, state = _load(args)
    except StateError as exc:
        emit(f"MISUSE: {exc}")
        return EXIT_MISUSE
    except LockBusy as exc:
        emit(f"BLOCKED: {exc}")
        return EXIT_BLOCKED
    try:
        if args.channel not in REVIEW_CHANNELS:
            emit(f"MISUSE: unknown channel {args.channel!r}; expected one of {REVIEW_CHANNELS}")
            return EXIT_MISUSE
        record = _round(state, args.round)
        if record is None:
            emit(f"MISUSE: this run has no review round {args.round}")
            return EXIT_MISUSE
        existing = [
            f
            for f in state.findings
            if f["round"] == args.round and f["channel"] == args.channel
        ]
        if existing:
            emit(
                f"MISUSE: round {args.round} channel {args.channel} already has "
                f"{len(existing)} recorded finding(s); it cannot also have none"
            )
            return EXIT_MISUSE
        if any(
            int(a["round"]) == args.round and a["channel"] == args.channel
            for a in state.triage
        ):
            emit(f"MISUSE: round {args.round} channel {args.channel} is already attested")
            return EXIT_MISUSE
        # What is being attested depends on what actually happened to that
        # channel. "It reported nothing" and "its outcome was never recorded"
        # are different statements, and the record must not blur them.
        status = channel_state(record, args.channel)
        state.triage.append(
            {
                "round": args.round,
                "channel": args.channel,
                "at": utc_now().isoformat(timespec="seconds"),
                "note": args.note.strip(),
                "channel_status": status,
                "incomplete": status != "completed",
            }
        )
        state.save(run_dir)
        if status == "completed":
            emit(f"attested: round {args.round} channel {args.channel} reported no findings")
        else:
            emit(
                f"attested: round {args.round} channel {args.channel} is {status} — "
                "recorded as accounted for, NOT as having reported nothing"
            )
        return EXIT_OK
    finally:
        lock.release()


def cmd_findings_resolve(args: argparse.Namespace) -> int:
    try:
        run_dir, lock, state = _load(args)
    except StateError as exc:
        emit(f"MISUSE: {exc}")
        return EXIT_MISUSE
    except LockBusy as exc:
        emit(f"BLOCKED: {exc}")
        return EXIT_BLOCKED
    try:
        target = next((f for f in state.findings if f["id"] == args.id), None)
        if target is None:
            emit(f"MISUSE: no finding {args.id!r} in this run")
            return EXIT_MISUSE
        if target.get("out_of_scope") and args.disposition in RESOLVED_DISPOSITIONS:
            emit(
                f"BLOCKED: {args.id} is marked out of the approved scope. This run "
                "may not change it, so it cannot be recorded as fixed; leave it "
                "awaiting adjudication and stop for authorisation."
            )
            return EXIT_BLOCKED
        target["disposition"] = args.disposition
        target["note"] = args.note.strip()
        target["resolved_at"] = utc_now().isoformat(timespec="seconds")
        state.save(run_dir)
        emit(f"{args.id}: {args.disposition} — {target['note']}")
        return EXIT_OK
    finally:
        lock.release()


def cmd_findings_list(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir).resolve()
    try:
        state = load_state(run_dir)
    except StateError as exc:
        emit(f"MISUSE: {exc}")
        return EXIT_MISUSE
    records = [Finding(**f) for f in state.findings]
    if args.pending:
        records = [f for f in records if not f.resolved]
    if args.json:
        emit(json.dumps([f.to_dict() for f in records], indent=2))
        return EXIT_OK
    if not records:
        emit("no findings recorded")
    for finding in records:
        emit(finding.one_line())
    blocking = [f for f in records if f.blocking and not f.resolved]
    if blocking:
        emit(f"{len(blocking)} unresolved blocking finding(s); delivery is not possible")
    return EXIT_OK


def add_findings_parser(sub, lock_arguments) -> None:
    findings = sub.add_parser(
        "findings",
        help="record what each review finding turned into (the release condition)",
    )
    inner = findings.add_subparsers(dest="findings_command", required=True)

    record = inner.add_parser("record", help="record a finding read from a prose channel")
    record.add_argument("--run-dir", required=True)
    record.add_argument("--round", type=int, required=True)
    record.add_argument("--channel", required=True)
    record.add_argument("--severity", required=True, choices=list(SEVERITIES))
    record.add_argument("--title", required=True)
    record.add_argument("--file", default="")
    record.add_argument("--line", type=int)
    record.add_argument("--body", default="")
    record.add_argument("--recommendation", default="")
    record.add_argument(
        "--out-of-scope",
        action="store_true",
        help="the change it asks for is outside the approved scope: it is "
        "recorded as awaiting the founder, never made by this run",
    )
    lock_arguments(record)
    record.set_defaults(func=cmd_findings_record)

    none = inner.add_parser(
        "none", help="attest that a channel reported no findings this round"
    )
    none.add_argument("--run-dir", required=True)
    none.add_argument("--round", type=int, required=True)
    none.add_argument("--channel", required=True)
    none.add_argument("--note", required=True, help="what you read, and where")
    lock_arguments(none)
    none.set_defaults(func=cmd_findings_none)

    resolve = inner.add_parser("resolve", help="record a disposition for one finding")
    resolve.add_argument("--run-dir", required=True)
    resolve.add_argument("--id", required=True)
    resolve.add_argument("--disposition", required=True, choices=list(DISPOSITIONS))
    resolve.add_argument(
        "--note", required=True, help="the commit, test or evidence behind this disposition"
    )
    lock_arguments(resolve)
    resolve.set_defaults(func=cmd_findings_resolve)

    listing = inner.add_parser("list", help="show findings and their dispositions")
    listing.add_argument("--run-dir", required=True)
    listing.add_argument("--json", action="store_true")
    listing.add_argument("--pending", action="store_true")
    listing.set_defaults(func=cmd_findings_list)
