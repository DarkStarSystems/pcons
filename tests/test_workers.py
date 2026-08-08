# SPDX-License-Identifier: MIT
"""Tests for persistent workers (pcons.workers)."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from pcons.workers import Worker, server

posix_only = pytest.mark.skipif(os.name == "nt", reason="workers need AF_UNIX and fork")


class TestWorkerIdentity:
    """Which actions may share a worker."""

    def test_same_setup_is_the_same_worker(self) -> None:
        assert (
            Worker(preload=["a", "b"]).identity == Worker(preload=["a", "b"]).identity
        )

    def test_order_does_not_matter(self) -> None:
        """The same modules are the same worker however they were listed."""
        assert (
            Worker(preload=["b", "a"]).identity == Worker(preload=["a", "b"]).identity
        )

    def test_different_preload_is_a_different_worker(self) -> None:
        assert Worker(preload=["a"]).identity != Worker(preload=["a", "b"]).identity

    def test_different_interpreter_is_a_different_worker(self) -> None:
        assert (
            Worker(preload=["a"], python="/usr/bin/python3").identity
            != Worker(preload=["a"], python="/opt/py/bin/python3").identity
        )

    def test_a_bare_module_name_is_accepted(self) -> None:
        assert Worker(preload="build123d").preload == ("build123d",)

    @posix_only
    def test_the_socket_fits_in_a_unix_address(self) -> None:
        """AF_UNIX truncates around 104 bytes, which is not a lot of path."""
        assert len(str(Worker(preload=["x"]).socket_path)) < 100

    @posix_only
    def test_the_socket_directory_is_private(self) -> None:
        """It accepts commands to run, so it is nobody else's business."""
        from pcons.workers import socket_dir

        assert socket_dir().stat().st_mode & 0o077 == 0


class TestLauncher:
    """How an action is routed through a worker."""

    def test_it_ends_with_a_separator(self) -> None:
        """The command follows, so the client can tell where its own args end."""
        assert Worker(preload=["x"]).launcher()[-1] == "--"

    def test_it_runs_the_client_not_the_server(self) -> None:
        tokens = Worker(preload=["x"]).launcher()

        assert Path(tokens[1]).name == "client.py"
        assert str(Worker(preload=["x"]).socket_path) in tokens

    def test_it_carries_the_preload_set(self) -> None:
        """The client passes these on when it has to start a worker."""
        assert "a,b" in Worker(preload=["b", "a"]).launcher()


class TestWhatAWorkerWillRun:
    """A worker stands in only for commands it can actually run."""

    def test_an_interpreter_and_a_script(self) -> None:
        assert server.script_argv(["/usr/bin/python3", "gen.py", "--out", "x"]) == [
            "gen.py",
            "--out",
            "x",
        ]

    def test_another_program_is_not_ours(self) -> None:
        assert server.script_argv(["cc", "-c", "main.c"]) is None

    def test_dash_c_is_not_ours(self) -> None:
        """Reimplementing the interpreter's own flags is how this goes wrong."""
        assert server.script_argv(["python3", "-c", "print(1)"]) is None

    def test_an_interpreter_with_nothing_to_run(self) -> None:
        assert server.script_argv(["python3"]) is None


def test_client_and_server_agree_on_the_environment_stamp() -> None:
    """They compare these to spot a moved virtualenv; drift means every
    worker looks stale and none is ever reused."""
    from pcons.workers import client

    assert client.environment_stamp(sys.executable) == server.environment_stamp(
        sys.executable
    )


@posix_only
class TestEndToEnd:
    """A real worker, over a real socket."""

    @staticmethod
    def _start_worker(tmp_path: Path) -> tuple[subprocess.Popen, Path]:
        sock = Path("/tmp") / f"pcons-test-{os.getpid()}.sock"
        proc = subprocess.Popen(
            [sys.executable, str(Path(server.__file__)), str(sock), "", "20"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not sock.exists():
            time.sleep(0.02)
        assert sock.exists(), "worker never started listening"
        return proc, sock

    @staticmethod
    def _run(
        sock: Path, script: Path, *args: str, preload: str = ""
    ) -> subprocess.CompletedProcess:
        from pcons.workers import client

        return subprocess.run(
            [
                sys.executable,
                str(Path(client.__file__)),
                str(sock),
                preload,
                "20",
                "--",
                sys.executable,
                str(script),
                *args,
            ],
            capture_output=True,
            text=True,
        )

    def test_output_and_exit_status_come_back(self, tmp_path: Path) -> None:
        script = tmp_path / "act.py"
        script.write_text(
            "import sys\n"
            "print('on stdout')\n"
            "print('on stderr', file=sys.stderr)\n"
            "sys.exit(7)\n"
        )
        proc, sock = self._start_worker(tmp_path)
        try:
            result = self._run(sock, script)
        finally:
            proc.kill()
            proc.wait()
            sock.unlink(missing_ok=True)

        assert result.returncode == 7
        assert "on stdout" in result.stdout
        assert "on stderr" in result.stderr

    def test_each_action_gets_a_pristine_process(self, tmp_path: Path) -> None:
        """The reason for forking: what one action does must not reach the next."""
        marker = tmp_path / "seen.txt"
        script = tmp_path / "act.py"
        script.write_text(
            "import pathlib, sys\n"
            "seen = getattr(sys, 'been_here', False)\n"
            f"pathlib.Path({str(marker)!r}).open('a').write(f'{{seen}}\\n')\n"
            "sys.been_here = True\n"
        )
        proc, sock = self._start_worker(tmp_path)
        try:
            self._run(sock, script)
            self._run(sock, script)
        finally:
            proc.kill()
            proc.wait()
            sock.unlink(missing_ok=True)

        assert marker.read_text().split() == ["False", "False"]

    def test_the_command_runs_directly_when_no_worker_answers(
        self, tmp_path: Path
    ) -> None:
        """A build with no reachable worker is a slower build, not a failure."""
        script = tmp_path / "act.py"
        script.write_text("print('ran anyway')\n")

        # A socket that is not there, and a preload that cannot import, so the
        # worker the client starts dies instead of listening -- and this test
        # leaves nothing running behind it.
        result = self._run(
            Path("/tmp") / "pcons-test-absent.sock",
            script,
            preload="pcons_no_such_module",
        )

        assert result.returncode == 0
        assert "ran anyway" in result.stdout
