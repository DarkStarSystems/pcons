# SPDX-License-Identifier: MIT
"""Tests for persistent workers (pcons.workers)."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from pcons.workers import Worker, python_server
from pcons.workers.python import PythonWorker

posix_only = pytest.mark.skipif(os.name == "nt", reason="workers need AF_UNIX")


class TestWorkerIdentity:
    """Which actions share a worker."""

    def test_the_same_start_command_is_the_same_worker(self) -> None:
        assert (
            Worker(command=["w", "-x"]).identity == Worker(command=["w", "-x"]).identity
        )

    def test_a_different_start_command_is_a_different_worker(self) -> None:
        assert Worker(command=["w"]).identity != Worker(command=["w", "-x"]).identity

    def test_key_separates_otherwise_identical_workers(self) -> None:
        assert (
            Worker(command=["w"]).identity != Worker(command=["w"], key=["v2"]).identity
        )

    def test_a_worker_needs_something_to_start(self) -> None:
        with pytest.raises(ValueError, match="needs a command"):
            Worker()

    @posix_only
    def test_the_socket_fits_in_a_unix_address(self) -> None:
        """AF_UNIX truncates around 104 bytes, which is not a lot of path."""
        assert len(str(Worker(command=["w"]).socket_path)) < 100

    @posix_only
    def test_the_socket_directory_is_private(self) -> None:
        """It accepts commands to run, so it is nobody else's business."""
        from pcons.workers import socket_dir

        assert socket_dir().stat().st_mode & 0o077 == 0


class TestLauncher:
    """How an action is routed through a worker."""

    def test_the_action_follows_a_separator(self) -> None:
        assert Worker(command=["w"]).launcher()[-1] == "--"

    def test_the_start_command_is_delimited_by_a_count(self) -> None:
        """A count, not a separator, so a start command may contain `--`."""
        tokens = Worker(command=["w", "--flag", "--"]).launcher()
        count = int(tokens[4])

        assert count == 3
        assert tokens[5 : 5 + count] == ["w", "--flag", "--"]

    def test_it_runs_the_client(self) -> None:
        tokens = Worker(command=["w"]).launcher()

        assert Path(tokens[1]).name == "client.py"
        assert tokens[2] == str(Worker(command=["w"]).socket_path)


class TestPythonWorker:
    """The bundled realization is a Worker like any other."""

    def test_it_starts_the_python_worker(self) -> None:
        worker = PythonWorker(preload=["json"])

        assert Path(worker.command[1]).name == "python_server.py"
        assert "--preload" in worker.command
        assert "json" in worker.command

    def test_preload_order_does_not_matter(self) -> None:
        """The same modules are the same worker however they were listed."""
        assert (
            PythonWorker(preload=["b", "a"]).identity
            == PythonWorker(preload=["a", "b"]).identity
        )

    def test_a_bare_module_name_is_accepted(self) -> None:
        assert "build123d" in PythonWorker(preload="build123d").command

    def test_setup_is_carried_through(self) -> None:
        """Readiness that is not an import: connect, claim, warm."""
        worker = PythonWorker(setup="mypkg.warmup:connect")

        assert "--setup" in worker.command
        assert "mypkg.warmup:connect" in worker.command

    def test_setup_makes_a_different_worker(self) -> None:
        assert (
            PythonWorker(preload=["json"]).identity
            != PythonWorker(preload=["json"], setup="m:f").identity
        )


class TestWhatThePythonWorkerWillRun:
    """It stands in only for commands it can actually run."""

    def test_an_interpreter_and_a_script(self) -> None:
        interpreter = str(Path("/usr/bin/python3"))
        assert python_server.script_argv([interpreter, "gen.py", "--out", "x"]) == [
            "gen.py",
            "--out",
            "x",
        ]

    def test_another_program_is_not_ours(self) -> None:
        assert python_server.script_argv(["cc", "-c", "main.c"]) is None

    def test_dash_c_is_not_ours(self) -> None:
        """Reimplementing the interpreter's own flags is how this goes wrong."""
        assert python_server.script_argv(["python3", "-c", "print(1)"]) is None

    def test_an_interpreter_with_nothing_to_run(self) -> None:
        assert python_server.script_argv(["python3"]) is None


def test_client_and_worker_agree_on_the_environment_stamp() -> None:
    """They compare these to spot a moved virtualenv; drift means every worker
    looks stale and none is ever reused."""
    from pcons.workers import client

    assert client.environment_stamp(sys.executable) == python_server.environment_stamp(
        sys.executable
    )


class TestEnvironmentStamp:
    """Telling one environment from another, and noticing when it changes."""

    @staticmethod
    def _fake_venv(root: Path, link_to: Path | None = None) -> Path:
        """A venv whose bin/python is a symlink out of it, as uv builds them."""
        (root / "bin").mkdir(parents=True)
        (root / "lib" / "python3.12" / "site-packages").mkdir(parents=True)
        (root / "pyvenv.cfg").write_text("home = /somewhere\n")
        target = link_to or Path(sys.executable)
        for name in ("python", "python3"):
            (root / "bin" / name).symlink_to(target)
        return root / "bin" / "python"

    def test_the_venv_is_not_followed_through_its_symlink(self, tmp_path: Path) -> None:
        """uv's bin/python points outside the venv; resolving it lands
        somewhere that knows nothing about the packages installed here."""
        python = self._fake_venv(tmp_path / "venv")

        assert python_server.venv_of(str(python)) == tmp_path / "venv"

    def test_an_installed_or_removed_package_is_noticed(self, tmp_path: Path) -> None:
        """What the stamp is actually for. `uv pip install` and a `uv sync`
        that removes packages both move site-packages and neither touches
        pyvenv.cfg, so a worker keyed on that alone serves stale library code
        until it times out."""
        python = self._fake_venv(tmp_path / "venv")
        before = python_server.environment_stamp(str(python))

        os.utime(tmp_path / "venv" / "lib" / "python3.12" / "site-packages", (1, 1))

        assert python_server.environment_stamp(str(python)) != before

    def test_a_recreated_venv_is_noticed(self, tmp_path: Path) -> None:
        python = self._fake_venv(tmp_path / "venv")
        before = python_server.environment_stamp(str(python))

        os.utime(tmp_path / "venv" / "pyvenv.cfg", (1, 1))

        assert python_server.environment_stamp(str(python)) != before

    def test_an_untouched_environment_stamps_the_same(self, tmp_path: Path) -> None:
        """Otherwise no worker would ever be reused."""
        python = self._fake_venv(tmp_path / "venv")

        assert python_server.environment_stamp(
            str(python)
        ) == python_server.environment_stamp(str(python))

    def test_python_and_python3_are_one_environment(self, tmp_path: Path) -> None:
        """Same interpreter, two names in one bin/ -- and a worker started by
        one name serving a client that named the other."""
        self._fake_venv(tmp_path / "venv")
        bin_dir = tmp_path / "venv" / "bin"

        assert python_server.environment_stamp(
            str(bin_dir / "python")
        ) == python_server.environment_stamp(str(bin_dir / "python3"))

    def test_a_missing_pyvenv_cfg_is_not_fatal(self, tmp_path: Path) -> None:
        """Not every interpreter lives in a venv."""
        assert python_server.environment_stamp("/usr/bin/python3")


class TestForkSafety:
    """Whether this process may fork at all."""

    def test_a_single_threaded_process_may_fork(self, monkeypatch) -> None:
        """Counts are injected: a test runner has threads of its own, so the
        ambient process is never the one being described."""
        monkeypatch.setattr(python_server.threading, "active_count", lambda: 1)
        monkeypatch.setattr(python_server, "os_thread_count", lambda: 1)

        assert python_server.fork_hazard() == ""

    def test_python_threads_are_a_hazard(self, monkeypatch) -> None:
        monkeypatch.setattr(python_server.threading, "active_count", lambda: 2)

        assert "Python threads" in python_server.fork_hazard()

    def test_native_threads_are_a_hazard(self, monkeypatch) -> None:
        """The ones threading.active_count() cannot see: OpenMP, a threaded
        BLAS -- exactly what a numpy/scipy preload set starts."""
        monkeypatch.setattr(python_server.threading, "active_count", lambda: 1)
        monkeypatch.setattr(python_server, "os_thread_count", lambda: 8)

        assert "8 native threads" in python_server.fork_hazard()

    def test_the_operating_system_is_asked_too(self) -> None:
        """threading.active_count() cannot see OpenMP or a threaded BLAS,
        which is exactly what a numpy/scipy preload set starts."""
        count = python_server.os_thread_count()

        assert count is None or count >= 1

    def test_an_unknown_thread_count_is_treated_as_unsafe(self, monkeypatch) -> None:
        """A corrupted child costs more than a slow build."""
        monkeypatch.setattr(python_server, "os_thread_count", lambda: None)

        assert python_server.fork_hazard() != ""


class TestRefusals:
    """What a worker declines, and whether it says why."""

    @staticmethod
    def _state() -> dict:
        return {"stamp": None, "hazard": ""}

    def test_an_action_for_another_environment(self) -> None:
        """Running it here would import a different set of packages."""
        other = Path("/elsewhere/.venv")
        request = {"argv": [str(other / "bin" / "python"), "act.py"]}

        reason = python_server._refusal(request, self._state())

        # Rendered through Path on both sides: the separator is the platform's.
        assert f"wants {other}" in reason

    def test_an_action_this_worker_cannot_run(self) -> None:
        request = {"argv": ["cc", "-c", "main.c"]}

        assert "not an interpreter" in python_server._refusal(request, self._state())

    def test_a_fork_hazard_refuses_everything(self) -> None:
        request = {"argv": [sys.executable, "act.py"]}
        state = {"stamp": None, "hazard": "the worker has 4 native threads"}

        assert python_server._refusal(request, state) == state["hazard"]

    def test_an_action_it_can_run(self) -> None:
        request = {"argv": [sys.executable, "act.py", "--out", "x"]}

        assert python_server._refusal(request, self._state()) == ""
        assert request["runnable"] == ["act.py", "--out", "x"]


# A worker that is not pcons's: it implements the protocol and nothing else,
# which is the claim this file exists to check.
FOREIGN_WORKER = """
import array, json, os, socket, sys

sock_path = sys.argv[-1]
tmp = sock_path + ".tmp"
srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
srv.bind(tmp)
os.chmod(tmp, 0o600)
srv.listen(8)
os.rename(tmp, sock_path)
srv.settimeout(float(os.environ.get("PCONS_WORKER_IDLE_TIMEOUT", "20")))
while True:
    try:
        conn, _ = srv.accept()
    except TimeoutError:
        break
    fds = array.array("i")
    msg, anc, _f, _a = conn.recvmsg(65536, socket.CMSG_SPACE(3 * fds.itemsize))
    for level, kind, data in anc:
        fds.frombytes(data[: len(data) - (len(data) % fds.itemsize)])
    os.write(fds[1], b"served by a foreign worker\\n")
    for fd in fds:
        os.close(fd)
    conn.sendall(json.dumps({"exit": 42}).encode() + b"\\n")
    conn.close()
"""


@posix_only
class TestEndToEnd:
    """Real workers, over real sockets."""

    @staticmethod
    def _socket(name: str) -> Path:
        return Path("/tmp") / f"pcons-test-{os.getpid()}-{name}.sock"

    @staticmethod
    def _start(command: list[str], sock: Path) -> subprocess.Popen:
        proc = subprocess.Popen(
            [*command, str(sock)],
            env=dict(os.environ, PCONS_WORKER_IDLE_TIMEOUT="20"),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not sock.exists():
            time.sleep(0.02)
        assert sock.exists(), "worker never started listening"
        return proc

    @staticmethod
    def _run(worker: Worker, sock: Path, *command: str) -> subprocess.CompletedProcess:
        tokens = worker.launcher()
        tokens[2] = str(sock)  # point at the socket this test controls
        return subprocess.run([*tokens, *command], capture_output=True, text=True)

    def test_output_and_exit_status_come_back(self, tmp_path: Path) -> None:
        script = tmp_path / "act.py"
        script.write_text(
            "import sys\n"
            "print('on stdout')\n"
            "print('on stderr', file=sys.stderr)\n"
            "sys.exit(7)\n"
        )
        worker = PythonWorker()
        sock = self._socket("py")
        proc = self._start(list(worker.command), sock)
        try:
            result = self._run(worker, sock, sys.executable, str(script))
        finally:
            proc.kill()
            proc.wait()
            sock.unlink(missing_ok=True)

        assert result.returncode == 7
        assert "on stdout" in result.stdout
        assert "on stderr" in result.stderr

    def test_each_action_gets_a_pristine_process(self, tmp_path: Path) -> None:
        """The contract's one hard requirement, checked on the bundled worker."""
        marker = tmp_path / "seen.txt"
        script = tmp_path / "act.py"
        script.write_text(
            "import pathlib, sys\n"
            "seen = getattr(sys, 'been_here', False)\n"
            f"pathlib.Path({str(marker)!r}).open('a').write(f'{{seen}}\\n')\n"
            "sys.been_here = True\n"
        )
        worker = PythonWorker()
        sock = self._socket("pristine")
        proc = self._start(list(worker.command), sock)
        try:
            self._run(worker, sock, sys.executable, str(script))
            self._run(worker, sock, sys.executable, str(script))
        finally:
            proc.kill()
            proc.wait()
            sock.unlink(missing_ok=True)

        assert marker.read_text().split() == ["False", "False"]

    def test_a_worker_pcons_knows_nothing_about(self, tmp_path: Path) -> None:
        """Anything implementing the protocol is a worker, not only ours."""
        foreign = tmp_path / "foreign_worker.py"
        foreign.write_text(FOREIGN_WORKER)
        worker = Worker(command=[sys.executable, str(foreign)])
        sock = self._socket("foreign")
        proc = self._start(list(worker.command), sock)
        try:
            result = self._run(worker, sock, "/bin/echo", "not run directly")
        finally:
            proc.kill()
            proc.wait()
            sock.unlink(missing_ok=True)

        assert result.returncode == 42  # the status the foreign worker chose
        assert "served by a foreign worker" in result.stdout

    def test_a_worker_named_differently_from_the_client(self, tmp_path: Path) -> None:
        """`bin/python` and `bin/python3` are one environment. Comparing the
        two names refused every request, which looked exactly like no worker."""
        bin_dir = Path(sys.executable).parent
        other_name = bin_dir / ("python" if sys.executable.endswith("3") else "python3")
        if not other_name.exists():
            pytest.skip("this interpreter has only one name")

        script = tmp_path / "act.py"
        script.write_text("print('served')\n")
        worker = PythonWorker(python=str(other_name))
        sock = self._socket("names")
        proc = self._start(list(worker.command), sock)
        try:
            result = self._run(worker, sock, str(other_name), str(script))
        finally:
            proc.kill()
            proc.wait()
            sock.unlink(missing_ok=True)

        assert result.returncode == 0
        assert "served" in result.stdout

    def test_the_command_runs_directly_when_no_worker_answers(
        self, tmp_path: Path
    ) -> None:
        """A build with no reachable worker is a slower build, not a failure."""
        script = tmp_path / "act.py"
        script.write_text("print('ran anyway')\n")
        # A start command that exits instead of listening, so this test leaves
        # nothing running behind it.
        worker = Worker(command=[sys.executable, "-c", "raise SystemExit(1)"])

        result = self._run(worker, self._socket("absent"), sys.executable, str(script))

        assert result.returncode == 0
        assert "ran anyway" in result.stdout
