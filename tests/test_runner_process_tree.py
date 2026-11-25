# SPDX-License-Identifier: MIT
"""Timeouts of ``pcons test`` with tests that start processes of their own.

Kept apart from ``tests/test_runner.py``, which is skipped on Windows.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import cast

import pytest

from pcons.test_runner import (
    PASS,
    TIMEOUT,
    TestResult,
    _finish_killed,
    run_one_test,
)
from pcons.util.process_tree import ProcessTree
from tests.support import gone, kill_pid, read_pid, write_spawner


def _run(script: Path, timeout: float) -> TestResult:
    test = {"name": "t", "command": [sys.executable, str(script)], "timeout": timeout}
    return run_one_test(test, script.parent)


def _time_out_after_spawn(script: Path, pid_file: Path) -> tuple[TestResult, int]:
    """Run *script* until its timeout, once its sleeper had time to start.

    A short timeout keeps the run fast. On a loaded machine it can expire
    before the interpreters are up, so it is retried once with a long one.
    """
    for timeout in (1, 10):
        result = _run(script, timeout)
        if pid_file.exists():
            break
    return result, timeout


@pytest.mark.skipif(sys.platform != "win32", reason="POSIX kill not implemented")
@pytest.mark.parametrize("how", ["direct", "orphan", "exited"])
def test_timeout_kills_the_whole_tree(tmp_path: Path, how: str) -> None:
    pid_file = tmp_path / "pid"
    script = write_spawner(tmp_path / "child.py", pid_file, how)
    result, timeout = _time_out_after_spawn(script, pid_file)
    pid = read_pid(pid_file, 0)
    try:
        assert result.status == TIMEOUT
        assert result.message == f"Killed after {timeout}s"
        assert "partial" in result.stdout
        assert result.duration < timeout + 4
        assert gone(pid, 10)
    finally:
        kill_pid(pid)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX reaches the child only")
def test_timeout_returns_at_once_on_posix(tmp_path: Path) -> None:
    pid_file = tmp_path / "pid"
    script = write_spawner(tmp_path / "child.py", pid_file, "direct")
    result, timeout = _time_out_after_spawn(script, pid_file)
    pid = read_pid(pid_file, 0)
    try:
        assert result.status == TIMEOUT
        assert result.duration < timeout + 4
    finally:
        kill_pid(pid)


def test_a_test_that_exits_before_its_timeout_leaves_its_daemon(
    tmp_path: Path,
) -> None:
    pid_file = tmp_path / "pid"
    result = _run(write_spawner(tmp_path / "child.py", pid_file, "daemon"), 30)
    pid = read_pid(pid_file, 0)
    try:
        assert result.status == PASS
        assert not gone(pid, 0)
    finally:
        kill_pid(pid)


class _FakeProc:
    def __init__(self, drained: bool) -> None:
        self.drained = drained
        self.waited = False

    def communicate(self, timeout: float | None = None) -> tuple[str, str]:
        if not self.drained:
            raise subprocess.TimeoutExpired("t", timeout or 0)
        return "out", "err"

    def wait(self) -> int:
        self.waited = True
        return -9


class _FakeTree:
    def __init__(self, reaches_descendants: bool, drained: bool = True) -> None:
        self.reaches_descendants = reaches_descendants
        self.proc = _FakeProc(drained)
        self.killed = False

    def kill(self) -> None:
        self.killed = True


def test_timeout_drains_the_output_when_the_kill_reaches_descendants() -> None:
    tree = _FakeTree(reaches_descendants=True)
    timed_out = subprocess.TimeoutExpired("t", 1)
    e = _finish_killed(cast(ProcessTree, tree), timed_out)
    assert tree.killed
    assert (e.stdout, e.stderr) == ("out", "err")
    assert not tree.proc.waited


def test_timeout_only_reaps_the_child_when_the_kill_reaches_it_alone() -> None:
    tree = _FakeTree(reaches_descendants=False)
    timed_out = subprocess.TimeoutExpired("t", 1)
    assert _finish_killed(cast(ProcessTree, tree), timed_out) is timed_out
    assert tree.killed
    assert tree.proc.waited


def test_timeout_gives_up_a_drain_that_times_out() -> None:
    tree = _FakeTree(reaches_descendants=True, drained=False)
    timed_out = subprocess.TimeoutExpired("t", 1)
    assert _finish_killed(cast(ProcessTree, tree), timed_out) is timed_out
    assert tree.proc.waited
