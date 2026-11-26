# SPDX-License-Identifier: MIT
"""Run a child process so that it can be killed with its descendants."""

from __future__ import annotations

import contextlib
import logging
import os
import signal
import subprocess
import sys
from collections.abc import Sequence
from typing import Any

logger = logging.getLogger(__name__)

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _CREATE_SUSPENDED = 0x00000004
    _PROCESS_TERMINATE = 0x0001
    _PROCESS_SET_QUOTA = 0x0100
    _THREAD_SUSPEND_RESUME = 0x0002
    _TH32CS_SNAPTHREAD = 0x00000004
    _JOB_OBJECT_LIMIT_BREAKAWAY_OK = 0x00000800
    _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9
    _INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
    _RESUME_FAILED = 0xFFFFFFFF

    class _BasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
            ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _IoCounters(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class _ExtendedLimitInformation(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimitInformation),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    class _ThreadEntry32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ThreadID", wintypes.DWORD),
            ("th32OwnerProcessID", wintypes.DWORD),
            ("tpBasePri", wintypes.LONG),
            ("tpDeltaPri", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
        ]

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    _kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    _kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    _kernel32.SetInformationJobObject.restype = wintypes.BOOL
    _kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    _kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _kernel32.TerminateJobObject.restype = wintypes.BOOL
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.OpenThread.restype = wintypes.HANDLE
    _kernel32.ResumeThread.argtypes = [wintypes.HANDLE]
    _kernel32.ResumeThread.restype = wintypes.DWORD
    _kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    _kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    _kernel32.Thread32First.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_ThreadEntry32),
    ]
    _kernel32.Thread32First.restype = wintypes.BOOL
    _kernel32.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ThreadEntry32)]
    _kernel32.Thread32Next.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL

    def _last_error() -> OSError:
        return ctypes.WinError(ctypes.get_last_error())

    def _job_holding(pid: int) -> int:
        job = _kernel32.CreateJobObjectW(None, None)
        if not job:
            raise _last_error()
        try:
            info = _ExtendedLimitInformation()
            info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_BREAKAWAY_OK
            if not _kernel32.SetInformationJobObject(
                job,
                _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
                ctypes.byref(info),
                ctypes.sizeof(info),
            ):
                raise _last_error()
            _assign(job, pid)
        except OSError:
            _kernel32.CloseHandle(job)
            raise
        return job

    def _assign(job: int, pid: int) -> None:
        process = _kernel32.OpenProcess(
            _PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, pid
        )
        if not process:
            raise _last_error()
        try:
            if not _kernel32.AssignProcessToJobObject(job, process):
                raise _last_error()
        finally:
            _kernel32.CloseHandle(process)

    def _resume(pid: int) -> None:
        snapshot = _kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPTHREAD, 0)
        if snapshot == _INVALID_HANDLE_VALUE:
            raise _last_error()
        try:
            resumed = False
            entry = _ThreadEntry32(dwSize=ctypes.sizeof(_ThreadEntry32))
            found = _kernel32.Thread32First(snapshot, ctypes.byref(entry))
            while found:
                if entry.th32OwnerProcessID == pid:
                    _resume_thread(entry.th32ThreadID)
                    resumed = True
                found = _kernel32.Thread32Next(snapshot, ctypes.byref(entry))
        finally:
            _kernel32.CloseHandle(snapshot)
        if not resumed:
            raise OSError(f"no thread to resume in process {pid}")

    def _resume_thread(thread_id: int) -> None:
        thread = _kernel32.OpenThread(_THREAD_SUSPEND_RESUME, False, thread_id)
        if not thread:
            raise _last_error()
        try:
            if _kernel32.ResumeThread(thread) == _RESUME_FAILED:
                raise _last_error()
        finally:
            _kernel32.CloseHandle(thread)


class ProcessTree:
    """A child process and every descendant the platform lets us reach.

    On Windows the child starts in a job object, so every process it starts
    joins the job and dies with it, unless it breaks away on purpose.
    Elsewhere the child leads a new session, so every process it starts is
    in its process group, unless it leaves on purpose (``setsid``,
    ``setpgid``). A new session also leaves the child out of signals sent to
    the caller's process group, such as Ctrl-C's; :meth:`send_signal` passes
    one on.

    Closing the tree releases its resources and never kills anything, so a
    process the child left running on purpose outlives the tree.

    Attributes:
        proc: The child process.
        reaches_descendants: Whether :meth:`kill` takes down the descendants
            too, so their hold on the child's pipes ends with the kill.
    """

    def __init__(self, command: Sequence[str], **popen_kwargs: Any) -> None:
        """Start *command*.

        Args:
            command: The program and its arguments.
            **popen_kwargs: Passed to :class:`subprocess.Popen`.

        Raises:
            OSError: The child could not be started.
        """
        self._job: int | None = None
        self.proc: subprocess.Popen[Any]
        if sys.platform == "win32":
            self.proc = self._spawn_in_job(command, popen_kwargs)
            self.reaches_descendants = self._job is not None
        else:
            self.proc = subprocess.Popen(
                command, start_new_session=True, **popen_kwargs
            )
            self.reaches_descendants = True

    if sys.platform == "win32":

        def _spawn_in_job(
            self, command: Sequence[str], popen_kwargs: dict[str, Any]
        ) -> subprocess.Popen[Any]:
            flags = popen_kwargs.pop("creationflags", 0) | _CREATE_SUSPENDED
            proc = subprocess.Popen(command, creationflags=flags, **popen_kwargs)
            try:
                self._job = _job_holding(proc.pid)
            except OSError as e:
                logger.warning(
                    "%s runs outside a job object, a kill will miss its descendants: %s",
                    command[0],
                    e,
                )
            try:
                _resume(proc.pid)
            except OSError:
                with proc:
                    proc.kill()
                self.close()
                raise
            return proc

    def kill(self) -> None:
        """Kill the child and, when :attr:`reaches_descendants`, its descendants.

        The descendants die even when the child itself already exited. Only
        call it on a tree whose processes should all stop. Never raises.
        """
        if sys.platform == "win32":
            if self._job is not None:
                _kernel32.TerminateJobObject(self._job, 1)
        else:
            self.send_signal(signal.SIGKILL)
        if self.proc.poll() is None:
            with contextlib.suppress(OSError):
                self.proc.kill()

    def send_signal(self, sig: int) -> None:
        """Send *sig* to the child's process group on POSIX, or to the child
        alone on Windows. Never raises.

        On POSIX the group outlives the child while any process in it lives,
        and its id isn't reused until then, so this reaches the descendants
        even after the child was reaped.
        """
        with contextlib.suppress(OSError):
            if sys.platform == "win32":
                self.proc.send_signal(sig)
            else:
                # macOS refuses a group of zombies with EPERM, not ESRCH.
                os.killpg(self.proc.pid, sig)

    def group_alive(self) -> bool:
        """Whether any process is left in the child's process group, such as
        a server the child started and left running. Always False on
        Windows, which has no process groups to signal."""
        if sys.platform == "win32":
            return False
        try:
            os.killpg(self.proc.pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:  # macOS, for a group of zombies
            return True
        return True

    def close(self) -> None:
        """Release what the tree holds. Descendants keep running.

        On Windows that is the job handle. Elsewhere it is the child's pipes,
        as :class:`subprocess.Popen` closes them on exit. On Windows the pipes
        stay with :meth:`subprocess.Popen.communicate`, whose reader threads
        may still be blocked on them.
        """
        if sys.platform == "win32":
            if self._job is not None:
                _kernel32.CloseHandle(self._job)
        else:
            for pipe in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
                if pipe is not None:
                    pipe.close()
        self._job = None

    def __enter__(self) -> ProcessTree:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
