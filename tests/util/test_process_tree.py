# SPDX-License-Identifier: MIT
"""Tests for pcons.util.process_tree."""

from __future__ import annotations

import signal
import subprocess
import sys
from pathlib import Path

import pytest

from pcons.util.process_tree import ProcessTree
from tests.support import gone, kill_pid, read_pid, write_spawner, zombie

CREATE_BREAKAWAY_FROM_JOB = 0x01000000
# The venv launcher runs the interpreter in a job of its own that allows breakaway,
# so with sys.executable this test passes even when our job forbids it.
BASE_PYTHON: str = getattr(sys, "_base_executable", sys.executable)


def test_reaches_descendants() -> None:
    with ProcessTree([sys.executable, "-c", "pass"]) as tree:
        tree.proc.wait()
        assert tree.reaches_descendants


def test_kill_reaches_an_orphaned_grandchild(tmp_path: Path) -> None:
    pid_file = tmp_path / "pid"
    script = write_spawner(tmp_path / "child.py", pid_file, "orphan")
    with ProcessTree(
        [sys.executable, str(script)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as tree:
        pid = read_pid(pid_file, 30)
        try:
            tree.kill()
            tree.proc.communicate(timeout=10)
            assert gone(pid, 10)
        finally:
            kill_pid(pid)


def test_kill_reaches_the_descendants_of_an_exited_child(tmp_path: Path) -> None:
    pid_file = tmp_path / "pid"
    script = write_spawner(tmp_path / "child.py", pid_file, "exited")
    with ProcessTree(
        [sys.executable, str(script)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as tree:
        pid = read_pid(pid_file, 30)
        try:
            tree.proc.wait(timeout=30)
            tree.kill()
            stdout, _ = tree.proc.communicate(timeout=10)
            assert "partial" in stdout
            assert gone(pid, 10)
        finally:
            kill_pid(pid)


@pytest.mark.skipif(sys.platform != "win32", reason="job objects are Windows only")
def test_a_child_may_break_away(tmp_path: Path) -> None:
    script = tmp_path / "child.py"
    script.write_text(
        "import subprocess, sys\n"
        f"subprocess.run([sys.executable, '-c', 'pass'],"
        f" creationflags={CREATE_BREAKAWAY_FROM_JOB}, check=True)\n"
    )
    with ProcessTree(
        [BASE_PYTHON, str(script)], stderr=subprocess.PIPE, text=True
    ) as tree:
        _, stderr = tree.proc.communicate(timeout=30)
    assert tree.proc.returncode == 0, stderr


def test_close_leaves_descendants_running(tmp_path: Path) -> None:
    pid_file = tmp_path / "pid"
    script = write_spawner(tmp_path / "child.py", pid_file, "daemon")
    tree = ProcessTree([sys.executable, str(script)])
    tree.proc.communicate(timeout=30)
    pid = read_pid(pid_file, 30)
    try:
        tree.close()
        assert not gone(pid, 0)
    finally:
        kill_pid(pid)


def test_kill_after_exit_does_not_raise() -> None:
    tree = ProcessTree([sys.executable, "-c", "pass"])
    tree.proc.wait()
    tree.kill()
    tree.kill()
    tree.close()
    tree.close()
    assert tree.proc.returncode == 0


def test_kill_stops_a_running_child() -> None:
    with ProcessTree([sys.executable, "-c", "import time; time.sleep(60)"]) as tree:
        tree.kill()
        assert tree.proc.wait(timeout=10) != 0


@pytest.mark.skipif(
    sys.platform == "win32", reason="Windows leaves the pipes to communicate"
)
def test_close_closes_the_pipes_of_a_killed_child() -> None:
    tree = ProcessTree(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    with pytest.raises(subprocess.TimeoutExpired):
        tree.proc.communicate(timeout=0.1)
    tree.kill()
    tree.proc.wait()
    tree.close()
    assert tree.proc.stdout is not None and tree.proc.stdout.closed
    assert tree.proc.stderr is not None and tree.proc.stderr.closed


@pytest.mark.skipif(sys.platform == "win32", reason="process groups are POSIX only")
def test_send_signal_reaches_the_process_group(tmp_path: Path) -> None:
    pid_file = tmp_path / "pid"
    script = write_spawner(tmp_path / "child.py", pid_file, "direct")
    with ProcessTree([sys.executable, str(script)], stdout=subprocess.DEVNULL) as tree:
        pid = read_pid(pid_file, 30)
        try:
            tree.send_signal(signal.SIGTERM)
            assert tree.proc.wait(timeout=10) == -signal.SIGTERM
            assert gone(pid, 10)
        finally:
            kill_pid(pid)


@pytest.mark.skipif(sys.platform == "win32", reason="process groups are POSIX only")
def test_group_alive_while_a_left_process_runs(tmp_path: Path) -> None:
    pid_file = tmp_path / "pid"
    script = write_spawner(tmp_path / "child.py", pid_file, "daemon")
    with ProcessTree([sys.executable, str(script)]) as tree:
        tree.proc.wait(timeout=30)
        pid = read_pid(pid_file, 30)
        try:
            assert tree.group_alive()
            kill_pid(pid)
            assert gone(pid, 10)
            # Without an init to reap it, the killed orphan is a zombie,
            # and a zombie still answers for its group.
            assert not tree.group_alive() or zombie(pid)
        finally:
            kill_pid(pid)


def test_group_is_not_alive_once_a_lone_child_exits() -> None:
    with ProcessTree([sys.executable, "-c", "pass"]) as tree:
        tree.proc.wait(timeout=30)
        assert not tree.group_alive()
