"""Killing a subprocess tree this run started, and nothing else.

``subprocess``'s own timeout kills the direct child only. A gate command is
usually a launcher — pytest spawning workers, npm spawning node — so the
launcher dies and its workers keep running against the shared database after
the run believes them dead.

Two platform mechanisms, both scoped to processes this run created:

* **Windows: a job object, joined before the child runs.** The child is
  created SUSPENDED, assigned to a job with ``KILL_ON_JOB_CLOSE``, and only
  then resumed. Assigning after the child is already running is a race: a
  fast launcher can spawn a worker in that window, and an existing descendant
  is not retroactively enrolled — so the job would report a clean kill while
  the escaped worker kept using the database. Containment failure is fatal:
  the suspended child is killed rather than released uncontained.
* **POSIX: a process group.** The child is spawned with
  ``start_new_session``, so it leads its own group from its first
  instruction, and the whole group is signalled. The group outlives its
  leader, so this reaches workers after the direct child has exited.

Cleanup is symmetric on both platforms: closing the tree kills whatever is
still in it, so a command that exited normally but left a worker behind
cannot outlive the gate's hold on the shared database.

``taskkill`` is not used. It walks the live parent chain, so once the direct
child has exited its orphans are unreachable from that PID — which is the
common case, not a corner case.
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

    _CREATE_SUSPENDED = 0x00000004
    _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    _TH32CS_SNAPTHREAD = 0x00000004
    _THREAD_SUSPEND_RESUME = 0x0002
    _INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

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

    class _THREADENTRY32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ThreadID", wintypes.DWORD),
            ("th32OwnerProcessID", wintypes.DWORD),
            ("tpBasePri", ctypes.c_long),
            ("tpDeltaPri", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
        ]


class ContainmentError(RuntimeError):
    """A child could not be contained, so it was not allowed to run."""


@dataclass
class TreeKill:
    """What actually happened, so the record is evidence and not a claim."""

    attempted: bool = False
    delivered: bool = False
    method: str = ""
    detail: str = ""


def _resume_process(pid: int) -> int:  # pragma: no cover - Windows only
    """Resume every thread of a freshly created suspended process.

    ``Popen`` closes the primary thread handle before returning, so the
    thread is reached through a snapshot instead. A process created suspended
    has exactly one thread, but every thread is resumed regardless rather
    than assuming that.
    """
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.OpenThread.restype = wintypes.HANDLE
    snapshot = kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPTHREAD, 0)
    if not snapshot or snapshot == _INVALID_HANDLE_VALUE:
        raise ContainmentError(
            f"could not enumerate threads to resume pid {pid} "
            f"({ctypes.get_last_error()})"
        )
    resumed = 0
    try:
        entry = _THREADENTRY32()
        entry.dwSize = ctypes.sizeof(_THREADENTRY32)
        if not kernel32.Thread32First(snapshot, ctypes.byref(entry)):
            raise ContainmentError(f"no threads found for pid {pid}")
        while True:
            if entry.th32OwnerProcessID == pid:
                handle = kernel32.OpenThread(
                    _THREAD_SUSPEND_RESUME, False, entry.th32ThreadID
                )
                if handle:
                    kernel32.ResumeThread(handle)
                    kernel32.CloseHandle(handle)
                    resumed += 1
            if not kernel32.Thread32Next(snapshot, ctypes.byref(entry)):
                break
    finally:
        kernel32.CloseHandle(snapshot)
    if resumed == 0:
        raise ContainmentError(f"no thread of pid {pid} could be resumed")
    return resumed


class ProcessTree:
    """Owns the containment and kill mechanism for ONE subprocess."""

    def __init__(self) -> None:
        self._job = None
        self._pgid: int | None = None

    # ---------------------------------------------------------------- spawn
    def popen_kwargs(self) -> dict:
        if _IS_WINDOWS:
            # Created suspended so that nothing it spawns can escape the job.
            return {"creationflags": _CREATE_SUSPENDED}
        # Its own session and process group from the first instruction, so
        # the whole tree can be signalled at once and the group survives its
        # leader.
        return {"start_new_session": True}

    def adopt(self, proc: subprocess.Popen) -> None:
        """Contain a just-spawned child, then let it run.

        Raises :class:`ContainmentError` if it cannot be contained. The
        caller must kill the child in that case: releasing an uncontained
        process would silently give back the guarantee this class exists to
        provide.
        """
        if not _IS_WINDOWS:
            self._pgid = proc.pid  # start_new_session makes the child the leader
            return
        self._assign_job(proc)  # pragma: no cover - Windows only
        _resume_process(proc.pid)  # pragma: no cover - Windows only

    def _assign_job(self, proc: subprocess.Popen) -> None:  # pragma: no cover
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            raise ContainmentError(
                f"CreateJobObject failed ({ctypes.get_last_error()})"
            )
        info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(
            job,
            _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(info),
            ctypes.sizeof(info),
        ):
            error = ctypes.get_last_error()
            kernel32.CloseHandle(job)
            raise ContainmentError(f"SetInformationJobObject failed ({error})")
        if not kernel32.AssignProcessToJobObject(job, int(proc._handle)):
            error = ctypes.get_last_error()
            kernel32.CloseHandle(job)
            raise ContainmentError(f"AssignProcessToJobObject failed ({error})")
        self._job = job

    # ----------------------------------------------------------------- kill
    def kill(self, proc: subprocess.Popen) -> TreeKill:
        """Terminate the tree. Safe to call after the direct child exited."""
        if _IS_WINDOWS:
            return self._kill_windows()  # pragma: no cover - Windows only
        return self._kill_posix()

    def _kill_windows(self) -> TreeKill:  # pragma: no cover - Windows only
        if self._job is None:
            return TreeKill(
                attempted=False,
                delivered=False,
                method="job-object",
                detail="the child was never contained",
            )
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        ok = kernel32.TerminateJobObject(self._job, 1)
        return TreeKill(
            attempted=True,
            delivered=bool(ok),
            method="job-object",
            detail="" if ok else f"TerminateJobObject failed ({ctypes.get_last_error()})",
        )

    def _kill_posix(self) -> TreeKill:
        if self._pgid is None:
            return TreeKill(
                attempted=False,
                delivered=False,
                method="killpg",
                detail="the child was never contained",
            )
        # Deliberately NOT guarded by proc.poll(): the process group outlives
        # its leader, and the surviving members are exactly what has to die.
        try:
            os.killpg(self._pgid, signal.SIGKILL)
            return TreeKill(attempted=True, delivered=True, method="killpg")
        except ProcessLookupError:
            return TreeKill(
                attempted=True, delivered=False, method="killpg", detail="no such group"
            )
        except (PermissionError, OSError) as exc:
            return TreeKill(
                attempted=True, delivered=False, method="killpg", detail=str(exc)
            )

    # ---------------------------------------------------------------- close
    def close(self) -> None:
        """Release the tree, killing anything still in it.

        Symmetric on both platforms, and it has to be: a command that exited
        normally while leaving a worker behind would otherwise keep using the
        shared database after the gate released its lock — which is the
        failure the lock exists to prevent, arriving by another route.
        """
        if _IS_WINDOWS:  # pragma: no cover - Windows only
            if self._job is not None:
                # KILL_ON_JOB_CLOSE: closing the last handle kills the rest.
                ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(self._job)
            self._job = None
            return
        if self._pgid is None:
            return
        pgid, self._pgid = self._pgid, None
        try:
            if pgid == os.getpgid(0):
                return  # start_new_session did not take; never signal our own group
        except OSError:  # pragma: no cover - getpgid on a platform without it
            return
        try:
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass
