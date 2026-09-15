"""Killing a subprocess tree this run started, and nothing else.

``subprocess``'s own timeout kills the direct child only. A gate command is
usually a launcher — pytest spawning workers, npm spawning node — so the
launcher dies and its workers keep running against the shared database after
the run believes them dead.

Two platform mechanisms, both scoped to processes this run created:

* **Windows: a job object.** The child is put in a job at spawn time with
  ``KILL_ON_JOB_CLOSE``, so terminating the job takes every descendant with
  it, and closing the job at the end catches anything left behind. This is
  used instead of ``taskkill /T`` because ``taskkill`` walks the live parent
  chain: once the direct child has exited, its orphaned grandchildren are no
  longer reachable from its PID and the kill silently does nothing. That is
  the common case, not a corner case — a launcher usually exits first.
* **POSIX: a process group.** The child is spawned with
  ``start_new_session``, and the whole group is signalled. The group outlives
  its leader, so this works after the direct child has exited too.

``taskkill`` remains the fallback for a Windows build where the job API is
unavailable, with its limitation recorded rather than hidden.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from dataclasses import dataclass

_IS_WINDOWS = sys.platform == "win32"

if _IS_WINDOWS:  # pragma: no cover - exercised on Windows only
    import ctypes
    from ctypes import wintypes

    _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
            ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.POINTER(wintypes.ULONG)),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", _IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]


@dataclass
class TreeKill:
    """What actually happened, so the record is evidence and not a claim."""

    attempted: bool = False
    delivered: bool = False
    method: str = ""
    detail: str = ""


class ProcessTree:
    """Owns the kill mechanism for ONE subprocess and its descendants."""

    def __init__(self) -> None:
        self._job = None

    # ---------------------------------------------------------------- spawn
    def popen_kwargs(self) -> dict:
        if _IS_WINDOWS:
            return {}
        # Its own session and process group, so the whole tree can be
        # signalled at once — and so the group survives its leader.
        return {"start_new_session": True}

    def adopt(self, proc: subprocess.Popen) -> None:
        """Put a just-spawned child under this object's control."""
        if not _IS_WINDOWS:
            return
        try:  # pragma: no cover - Windows only
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CreateJobObjectW.restype = wintypes.HANDLE
            job = kernel32.CreateJobObjectW(None, None)
            if not job:
                return
            info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not kernel32.SetInformationJobObject(
                job,
                _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                ctypes.byref(info),
                ctypes.sizeof(info),
            ):
                kernel32.CloseHandle(job)
                return
            if not kernel32.AssignProcessToJobObject(job, int(proc._handle)):
                kernel32.CloseHandle(job)
                return
            self._job = job
        except (OSError, AttributeError, ValueError):
            self._job = None

    # ----------------------------------------------------------------- kill
    def kill(self, proc: subprocess.Popen) -> TreeKill:
        """Terminate the tree. Safe to call after the direct child exited."""
        if _IS_WINDOWS:
            return self._kill_windows(proc)
        return self._kill_posix(proc)

    def _kill_windows(self, proc: subprocess.Popen) -> TreeKill:  # pragma: no cover
        if self._job is not None:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            ok = kernel32.TerminateJobObject(self._job, 1)
            return TreeKill(
                attempted=True,
                delivered=bool(ok),
                method="job-object",
                detail="" if ok else f"TerminateJobObject failed ({ctypes.get_last_error()})",
            )
        if proc.poll() is not None:
            # taskkill walks the LIVE parent chain, so with the direct child
            # already gone its orphans cannot be reached from this PID. Say
            # so rather than reporting a kill that did not happen.
            return TreeKill(
                attempted=False,
                delivered=False,
                method="taskkill",
                detail="the direct child had already exited and no job object was "
                "available, so any surviving descendants could not be reached",
            )
        result = subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            capture_output=True,
            timeout=60,
            check=False,
        )
        return TreeKill(
            attempted=True,
            delivered=result.returncode == 0,
            method="taskkill",
            detail=(result.stderr or b"").decode("utf-8", "replace").strip(),
        )

    def _kill_posix(self, proc: subprocess.Popen) -> TreeKill:
        # Deliberately NOT guarded by proc.poll(): the process group outlives
        # its leader, and the surviving members are exactly what has to die.
        try:
            os.killpg(proc.pid, signal.SIGKILL)
            return TreeKill(attempted=True, delivered=True, method="killpg")
        except ProcessLookupError:
            return TreeKill(
                attempted=True, delivered=False, method="killpg", detail="no such group"
            )
        except (PermissionError, OSError) as exc:
            try:
                proc.kill()
            except OSError:
                pass
            return TreeKill(
                attempted=True, delivered=False, method="killpg", detail=str(exc)
            )

    # ---------------------------------------------------------------- close
    def close(self) -> None:
        """Release the job handle.

        With KILL_ON_JOB_CLOSE this also kills anything still in the job, so
        a command that exited normally but left a daemon behind does not
        leave it running against the shared database.
        """
        if _IS_WINDOWS and self._job is not None:  # pragma: no cover - Windows only
            ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(self._job)
        self._job = None
