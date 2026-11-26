# SPDX-License-Identifier: MIT
"""Timeouts of ``pcons test`` with tests that start processes of their own.

Kept apart from ``tests/test_runner.py``, which is skipped on Windows.
"""

from __future__ import annotations

import codecs
import io
import locale
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import cast

import pytest

from pcons import test_runner
from pcons.test_runner import (
    PASS,
    TIMEOUT,
    TestResult,
    _as_text,
    _finish_killed,
    _InFlight,
    _passing_on_signals,
    run_all,
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
            raise subprocess.TimeoutExpired("t", timeout or 0, output=b"read so far")
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
    e = _finish_killed(cast(ProcessTree, tree), timed_out)
    assert tree.proc.waited
    assert e.timeout == 1
    assert (e.stdout, e.stderr) == (b"read so far", None)


def test_output_that_is_not_text_is_replaced(tmp_path: Path) -> None:
    script = tmp_path / "binary.py"
    script.write_text("import sys\nsys.stdout.buffer.write(b'\\xff\\xfe bad\\n')\n")
    result = _run(script, 30)
    assert result.status == PASS
    assert " bad" in result.stdout


# What Popen's text mode decodes with: UTF-8 in UTF-8 mode, else the locale's.
TEXT_ENCODING = io.text_encoding(None)
if TEXT_ENCODING == "locale":
    TEXT_ENCODING = locale.getencoding()


@pytest.mark.skipif(
    codecs.lookup(TEXT_ENCODING).name != "utf-8", reason="needs UTF-8 text"
)
def test_output_cut_partway_through_a_character_is_kept() -> None:
    assert _as_text("café".encode()[:-1]) == "caf�"
    assert _as_text("text") == "text"
    assert _as_text(None) == ""


@pytest.mark.skipif(sys.platform == "win32", reason="sessions are POSIX only")
def test_timeout_keeps_the_output_when_a_descendant_escapes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(test_runner, "_DRAIN_SECONDS", 0.5)
    pid_file = tmp_path / "pid"
    script = write_spawner(tmp_path / "child.py", pid_file, "escaped")
    result, timeout = _time_out_after_spawn(script, pid_file)
    pid = read_pid(pid_file, 0)
    try:
        assert result.status == TIMEOUT
        assert result.stdout == "partial\n"
        assert result.duration < timeout + 4
        assert not gone(pid, 0)
    finally:
        kill_pid(pid)


class _RecordingTree:
    def __init__(self, left_running: bool = False) -> None:
        self.signals: list[int] = []
        self.left_running = left_running

    def send_signal(self, sig: int) -> None:
        self.signals.append(sig)

    def group_alive(self) -> bool:
        return self.left_running


def test_a_test_started_after_a_signal_still_gets_it() -> None:
    in_flight = _InFlight()
    before, after = _RecordingTree(), _RecordingTree()
    with in_flight.track(cast(ProcessTree, before)):
        in_flight.send_signal(signal.SIGINT)
    with in_flight.track(cast(ProcessTree, after)):
        pass
    in_flight.send_signal(signal.SIGTERM)
    assert before.signals == [signal.SIGINT]
    assert after.signals == [signal.SIGINT]


def test_a_finished_test_that_left_processes_still_gets_signals() -> None:
    in_flight = _InFlight()
    server, done = _RecordingTree(left_running=True), _RecordingTree()
    for tree in (server, done):
        with in_flight.track(cast(ProcessTree, tree)):
            pass
    in_flight.send_signal(signal.SIGINT)
    assert server.signals == [signal.SIGINT]
    assert done.signals == []


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals only")
def test_passing_on_signals_restores_the_handlers_and_leaves_ignored_ones() -> None:
    previous = signal.signal(signal.SIGHUP, signal.SIG_IGN)
    try:
        before = [signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)]
        with _passing_on_signals(_InFlight()):
            assert signal.getsignal(signal.SIGHUP) is signal.SIG_IGN
            assert signal.getsignal(signal.SIGTERM) not in before
        after = [signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)]
        assert after == before
    finally:
        signal.signal(signal.SIGHUP, previous)


def _run_all_signalled(
    tmp_path: Path, sig: int, first: dict | None = None
) -> tuple[list[TestResult], float]:
    """Run one 60 s test with run_all, after the test *first* if given, and
    send *sig* to this process once the 60 s test has started."""
    started = tmp_path / "started"
    script = tmp_path / "sleep.py"
    script.write_text(
        f"import time\nopen({str(started)!r}, 'w').close()\ntime.sleep(60)\n"
    )

    def signal_once_started() -> None:
        deadline = time.monotonic() + 30
        while not started.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        os.kill(os.getpid(), sig)

    test: dict = {"name": "t", "command": [sys.executable, str(script)]}
    tests = [test]
    if first is not None:
        test["depends_on"] = [first["name"]]
        tests.insert(0, first)
    sender = threading.Thread(target=signal_once_started)
    sender.start()
    start = time.monotonic()
    try:
        results = run_all(
            tests,
            tmp_path,
            jobs=1,
            stop_on_fail=False,
            on_start=lambda _t: None,
            on_finish=lambda _t, _r: None,
        )
    finally:
        sender.join()
    return results, time.monotonic() - start


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals only")
def test_ctrl_c_reaches_the_tests(tmp_path: Path) -> None:
    start = time.monotonic()
    with pytest.raises(KeyboardInterrupt):
        _run_all_signalled(tmp_path, signal.SIGINT)
    assert time.monotonic() - start < 30


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals only")
def test_ctrl_c_reaches_what_a_finished_fixture_left_running(tmp_path: Path) -> None:
    pid_file = tmp_path / "pid"
    script = write_spawner(tmp_path / "server_start.py", pid_file, "daemon")
    fixture = {"name": "server.start", "command": [sys.executable, str(script)]}
    try:
        with pytest.raises(KeyboardInterrupt):
            _run_all_signalled(tmp_path, signal.SIGINT, first=fixture)
        assert gone(read_pid(pid_file, 0), 10)
    finally:
        if pid_file.exists():
            kill_pid(read_pid(pid_file, 0))


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals only")
def test_sigterm_reaches_the_tests_then_its_old_handler(tmp_path: Path) -> None:
    caught: list[int] = []
    previous = signal.signal(signal.SIGTERM, lambda sig, _frame: caught.append(sig))
    try:
        results, duration = _run_all_signalled(tmp_path, signal.SIGTERM)
    finally:
        signal.signal(signal.SIGTERM, previous)
    assert caught == [signal.SIGTERM]
    assert results[0].message == "killed by SIGTERM"
    assert duration < 30
