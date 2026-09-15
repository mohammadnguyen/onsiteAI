"""Mutual exclusion between concurrent handoff commands.

Two different resources need protecting and they are not the same resource:

* **One run's state file.** Two commands operating the same run interleave
  read-modify-write on ``run.json``. A ``stop`` that lands while a ``review``
  is in flight is silently undone when the review saves the state it loaded
  minutes earlier, and two reviews started together claim the same round
  number and overwrite each other's archived output.

* **The shared test database.** Several of this repository's suites talk to
  one PostgreSQL instance. Runs in different worktrees have different run
  directories, so a per-run lock does not help: concurrent gates corrupt
  each other's schema (observed here as
  ``asyncpg cache lookup failed for type NNNN``). That lock therefore lives
  outside any run directory, keyed by a name the brief declares.

Both are advisory file locks created with ``O_EXCL``. A holder is never
signalled, killed or waited on beyond a timeout: stopping another session or
a shared runtime is outside what this workflow is authorised to do. When a
lock is held the command reports who holds it and stops.

A stale lock (the holder crashed) is not detected automatically either.
Liveness probes are unreliable across platforms, and a wrong guess here
either kills a live run's serialisation or blocks forever; instead the
operator passes ``--break-lock`` and the break is recorded in the run.
"""

from __future__ import annotations

import json
import os
import socket
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

LOCK_DIR_ENV = "REVIEW_HANDOFF_LOCK_DIR"
DEFAULT_SHARED_LOCK_NAME = "shared-test-database"
# The gate is the long pole: a full backend suite here takes minutes, so a
# second run must be prepared to queue rather than give up immediately.
DEFAULT_GATE_LOCK_WAIT_SECONDS = 45 * 60
POLL_SECONDS = 0.5


class LockBusy(RuntimeError):
    """Another process holds the lock. Not an error to route around."""

    def __init__(self, path: Path, holder: dict | None, waited: float):
        self.path = Path(path)
        self.holder = holder or {}
        self.waited = waited
        who = (
            f"pid {self.holder.get('pid')} on {self.holder.get('host')} "
            f"({self.holder.get('purpose') or 'unknown purpose'}"
            + (f", run {self.holder['run_id']}" if self.holder.get("run_id") else "")
            + f", held since {self.holder.get('acquired_at')})"
            if self.holder
            else "an unidentified process"
        )
        super().__init__(
            f"{self.path.name} is held by {who}"
            + (f" after waiting {waited:.0f}s" if waited else "")
        )


def shared_lock_dir() -> Path:
    override = os.environ.get(LOCK_DIR_ENV)
    if override:
        return Path(override)
    return Path(tempfile.gettempdir()) / "review-handoff-locks"


def shared_lock_path(name: str) -> Path:
    slug = "".join(c if c.isalnum() or c in "-_" else "-" for c in name).strip("-")
    return shared_lock_dir() / f"{slug or 'shared'}.lock"


def run_lock_path(run_dir: Path) -> Path:
    return Path(run_dir) / "run.lock"


@dataclass
class FileLock:
    """An advisory lock file. Acquire it, or learn who has it."""

    path: Path
    purpose: str = ""
    run_id: str = ""
    _token: str = ""

    def _holder(self) -> dict | None:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def _try_once(self) -> bool:
        token = uuid.uuid4().hex
        payload = json.dumps(
            {
                "token": token,
                "pid": os.getpid(),
                "host": socket.gethostname(),
                "purpose": self.purpose,
                "run_id": self.run_id,
                "acquired_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "+00:00",
            },
            indent=2,
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        self._token = token
        return True

    def acquire(self, *, wait_seconds: float = 0.0, break_stale: bool = False) -> None:
        """Take the lock, waiting at most ``wait_seconds``.

        ``break_stale`` deletes an existing lock file once before retrying.
        It is deliberately explicit and never inferred: the holder process is
        left completely alone either way.
        """
        started = time.monotonic()
        if break_stale:
            try:
                self.path.unlink()
            except OSError:
                pass
        while True:
            if self._try_once():
                return
            waited = time.monotonic() - started
            if waited >= wait_seconds:
                raise LockBusy(self.path, self._holder(), waited)
            time.sleep(min(POLL_SECONDS, max(0.0, wait_seconds - waited)))

    def release(self) -> None:
        """Drop the lock, but only if this process still owns it.

        The ownership token stops a broken lock from being deleted twice: if
        someone else broke ours and took it, releasing must not remove their
        file.
        """
        if not self._token:
            return
        holder = self._holder()
        if holder is not None and holder.get("token") != self._token:
            self._token = ""
            return
        try:
            self.path.unlink()
        except OSError:
            pass
        self._token = ""

    def __enter__(self) -> FileLock:
        return self

    def __exit__(self, *exc_info) -> None:
        self.release()
