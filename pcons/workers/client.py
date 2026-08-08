# SPDX-License-Identifier: MIT
"""What ninja actually runs: hand the command to a worker, or just run it.

    client.py <socket> <idle timeout> <n> <start command (n tokens)> -- <command>

The client side of ``docs/worker-protocol.md``. Started once per action, so it
must start fast: standard library only, and no pcons import, which would cost
more than this whole hop.
"""

from __future__ import annotations

import array
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

#: Set PCONS_WORKER_DEBUG=1 to keep the worker's stderr and hear why an
#: action fell back. Without it, a refusal looks exactly like no worker at all.
DEBUG = bool(os.environ.get("PCONS_WORKER_DEBUG"))

CONNECT_TIMEOUT = 5.0
STARTUP_TIMEOUT = 120.0  # a worker is slow to start; that is why it exists


def note(reason: str) -> None:
    """Say why a worker was not used, when anyone asked to be told."""
    if DEBUG:
        print(f"pcons worker: running directly ({reason})", file=sys.stderr)


def run_directly(argv: list[str], reason: str = "no worker") -> int:
    """Run the command in this process's place, as if no worker existed."""
    note(reason)
    os.execvp(argv[0], argv)
    return 1  # not reached


def connect(sock_path: Path) -> socket.socket | None:
    """Connect to a listening worker, or return None if there is not one."""
    if not sock_path.exists():
        return None
    conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    conn.settimeout(CONNECT_TIMEOUT)
    try:
        conn.connect(str(sock_path))
    except OSError:
        conn.close()
        return None
    return conn


def spawn(
    sock_path: Path, start: list[str], idle_timeout: str
) -> subprocess.Popen | None:
    """Start a worker and detach from it; it outlives this build.

    The socket path is appended to whatever the project asked to be run, and
    the idle timeout goes in the environment: a worker needs both, and neither
    is worth making every worker parse for itself.
    """
    env = dict(os.environ, PCONS_WORKER_IDLE_TIMEOUT=idle_timeout)
    try:
        return subprocess.Popen(  # noqa: S603
            [*start, str(sock_path)],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            # A worker is not a place to report to the terminal -- except when
            # debugging one, where its stderr is the only thing that explains
            # why it would not start.
            stderr=None if DEBUG else subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        return None  # no worker, then; the caller runs the command itself


def wait_for(
    sock_path: Path, deadline: float, started: subprocess.Popen | None
) -> socket.socket | None:
    """Wait for a worker to start listening, ours or a concurrent action's.

    A worker we started that exits before it listens -- an import it cannot
    satisfy, most likely -- ends the wait immediately. Waiting out the full
    timeout instead would add it to *every* action of a build that is going to
    run its commands directly anyway.
    """
    import time

    while time.monotonic() < deadline:
        conn = connect(sock_path)
        if conn is not None:
            return conn
        if started is not None and started.poll() is not None:
            return None
        time.sleep(0.05)
    return None


def venv_of(python: str) -> Path:
    """The environment an interpreter belongs to.

    Deliberately *not* resolved: uv's ``.venv/bin/python`` is a symlink to an
    interpreter that lives outside the venv and knows nothing about the
    packages installed in it.
    """
    return Path(python).parent.parent


def package_dirs(venv: Path) -> list[Path]:
    """Where an environment keeps its packages.

    Globbed rather than derived: the version in ``lib/python3.x`` belongs to
    the interpreter, and asking it would mean starting one.
    """
    return sorted(venv.glob("lib/python*/site-packages")) + sorted(
        venv.glob("Lib/site-packages")  # Windows
    )


def environment_stamp(python: str) -> str:
    """A fingerprint of the environment a worker serves.

    Named by directory rather than by executable, because ``python`` and
    ``python3`` in one ``bin/`` are the same environment.

    Fingerprinted by what installing and uninstalling actually moves, which is
    ``site-packages``. ``pyvenv.cfg`` is written once when the venv is created
    and never touched again -- neither `uv pip install` nor a `uv sync` that
    removes packages changes it -- so a worker keyed on that alone would go on
    serving last week's library from memory, which is the one thing the stamp
    exists to prevent.
    """
    venv = venv_of(python)
    marks = []
    for candidate in [venv / "pyvenv.cfg", *package_dirs(venv)]:
        try:
            marks.append(str(candidate.stat().st_mtime_ns))
        except OSError:
            pass  # not every interpreter lives in a virtualenv
    return f"{venv}:{'.'.join(marks)}" if marks else str(venv)


def submit(conn: socket.socket, argv: list[str], worker_python: str) -> dict | None:
    """Send one request, with our own stdio, and wait for the verdict.

    Passing the file descriptors means the command writes straight to ninja's
    pipe: nothing copies its output, and stdout and stderr keep their order.
    """
    request = {
        "argv": argv,
        "cwd": os.getcwd(),
        "env": dict(os.environ),
        # The worker's environment, not this process's: pcons may well be
        # running from somewhere else entirely (uvx, a global install), and
        # what matters is the environment the action will run in.
        "stamp": environment_stamp(worker_python),
    }
    fds = array.array("i", [0, 1, 2])
    try:
        conn.sendmsg(
            [json.dumps(request).encode("utf-8")],
            [(socket.SOL_SOCKET, socket.SCM_RIGHTS, fds)],
        )
        conn.settimeout(None)  # the action itself may take as long as it takes
        answer = b""
        while not answer.endswith(b"\n"):
            chunk = conn.recv(4096)
            if not chunk:
                break
            answer += chunk
    except OSError:
        return None
    if not answer:
        return None
    try:
        return json.loads(answer.decode("utf-8"))
    except ValueError:
        return None


def main(argv: list[str]) -> int:
    sock_path = Path(argv[0])
    idle_timeout = argv[1]
    start_argc = int(argv[2])
    start = argv[3 : 3 + start_argc]
    command = argv[3 + start_argc + 1 :]  # skip the "--" that follows

    if os.name == "nt" or not hasattr(socket, "AF_UNIX"):
        return run_directly(command, "no AF_UNIX on this platform")

    conn = connect(sock_path)
    if conn is None:
        started = spawn(sock_path, start, idle_timeout)
        import time

        conn = wait_for(sock_path, time.monotonic() + STARTUP_TIMEOUT, started)
    if conn is None:
        return run_directly(command, "no worker is listening")

    worker_python = start[0] if start else sys.executable
    with conn:
        answer = submit(conn, command, worker_python)

    if answer is None or "exit" not in answer:
        # Refused (a stale worker, or one that cannot fork), or died mid-run.
        # Either way the command has not run, so run it.
        reason = (answer or {}).get("error") or "the worker did not answer"
        return run_directly(command, reason)
    exit_code = answer["exit"]
    return exit_code if isinstance(exit_code, int) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
