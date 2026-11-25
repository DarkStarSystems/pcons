# SPDX-License-Identifier: MIT
"""How pcons.util.process_tree handles a failing Win32 call.

Each test makes one kernel32 call fail and checks what the caller sees.
Windows only: the module has no Win32 calls elsewhere.
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Callable
from typing import Any

import pytest

if sys.platform == "win32":
    import ctypes

    import pcons.util.process_tree as process_tree
    from pcons.util.process_tree import ProcessTree

    QUOTA_ERROR = 1816
    HANDLE_MAKERS = ("CreateJobObjectW", "OpenProcess", "OpenThread")

    class FailingKernel32:
        """kernel32 with one function failing, recording every handle."""

        def __init__(self, real: Any, failing: str, result: object) -> None:
            self._real = real
            self._failing = failing
            self._result = result
            self.opened: list[int] = []
            self.closed: list[int] = []

        def __getattr__(self, name: str) -> Callable[..., Any]:
            if name == self._failing:
                return self._fail
            real = getattr(self._real, name)
            if name == "CloseHandle":
                return lambda handle: self._close(real, handle)
            if name in HANDLE_MAKERS or name == "CreateToolhelp32Snapshot":
                return lambda *args: self._open(real, *args)
            return real

        def _fail(self, *args: object) -> object:
            ctypes.set_last_error(QUOTA_ERROR)
            return self._result

        def _open(self, real: Callable[..., Any], *args: object) -> Any:
            handle = real(*args)
            if handle and handle != process_tree._INVALID_HANDLE_VALUE:
                self.opened.append(handle)
            return handle

        def _close(self, real: Callable[..., Any], handle: int) -> Any:
            self.closed.append(handle)
            return real(handle)

    @pytest.fixture
    def popens(monkeypatch: pytest.MonkeyPatch) -> list[subprocess.Popen[Any]]:
        started: list[subprocess.Popen[Any]] = []
        real_popen = subprocess.Popen

        def recording_popen(*args: Any, **kwargs: Any) -> subprocess.Popen[Any]:
            proc = real_popen(*args, **kwargs)
            started.append(proc)
            return proc

        monkeypatch.setattr(subprocess, "Popen", recording_popen)
        return started

    def failing_kernel32(
        monkeypatch: pytest.MonkeyPatch, name: str, result: object
    ) -> FailingKernel32:
        kernel32 = FailingKernel32(process_tree._kernel32, name, result)
        monkeypatch.setattr(process_tree, "_kernel32", kernel32)
        return kernel32

    @pytest.mark.parametrize(
        "name",
        [
            "CreateJobObjectW",
            "SetInformationJobObject",
            "OpenProcess",
            "AssignProcessToJobObject",
        ],
    )
    def test_a_failed_job_setup_warns_and_runs_the_child_alone(
        monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, name: str
    ) -> None:
        kernel32 = failing_kernel32(monkeypatch, name, 0)
        with caplog.at_level("WARNING", logger="pcons.util.process_tree"):
            tree = ProcessTree([sys.executable, "-c", "import time; time.sleep(60)"])
        with tree:
            assert not tree.reaches_descendants
            assert f"[WinError {QUOTA_ERROR}]" in caplog.text
            assert "outside a job object" in caplog.text
            tree.kill()
            assert tree.proc.wait(timeout=10) != 0
        assert sorted(kernel32.opened) == sorted(kernel32.closed)

    @pytest.mark.parametrize(
        ("name", "result", "error"),
        [
            (
                "CreateToolhelp32Snapshot",
                process_tree._INVALID_HANDLE_VALUE,
                f"[WinError {QUOTA_ERROR}]",
            ),
            ("OpenThread", 0, f"[WinError {QUOTA_ERROR}]"),
            ("ResumeThread", 0xFFFFFFFF, f"[WinError {QUOTA_ERROR}]"),
            ("Thread32First", 0, "no thread to resume"),
        ],
    )
    def test_a_failed_resume_kills_the_child_and_raises(
        monkeypatch: pytest.MonkeyPatch,
        popens: list[subprocess.Popen[Any]],
        name: str,
        result: object,
        error: str,
    ) -> None:
        kernel32 = failing_kernel32(monkeypatch, name, result)
        with pytest.raises(OSError, match=re.escape(error)):
            ProcessTree(
                [sys.executable, "-c", "print('ran')"],
                stdout=subprocess.PIPE,
                text=True,
            )
        [proc] = popens
        assert proc.returncode is not None
        assert proc.returncode != 0
        assert proc.stdout is not None and proc.stdout.closed
        assert kernel32.opened
        assert sorted(kernel32.opened) == sorted(kernel32.closed)
