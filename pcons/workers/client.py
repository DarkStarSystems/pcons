# SPDX-License-Identifier: MIT
"""What ninja actually runs: hand the command to a worker, or just run it.

    client.py <socket> <modules> <idle timeout> -- <command> [args...]

Started once per action, so it must start fast: standard library only, and no
pcons import (which costs more than this whole hop). It never fails a build on
its own account -- anything unexpected and it execs the command directly, which
is also what happens under plain ninja, in CI, or on a platform without fork.
The worker is an optimization, and a build that cannot reach one is still a
correct build, only a slower one.
"""

from __future__ import annotations

import array
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

CONNECT_TIMEOUT = 5.0
STARTUP_TIMEOUT = 120.0  # a worker is slow to start; that is why it exists


def run_directly(argv: list[str]) -> int:
    """Run the command in this process's place, as if no worker existed."""
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


def spawn(sock_path: Path, modules: str, idle_timeout: str) -> subprocess.Popen | None:
    """Start a worker and detach from it; it outlives this build."""
    server = Path(__file__).parent / "server.py"
    try:
        return subprocess.Popen(  # noqa: S603
            [sys.executable, str(server), str(sock_path), modules, idle_timeout],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
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


def environment_stamp(python: str) -> str:
    """Must agree with the server's, or the worker is serving stale code."""
    config = Path(python).resolve().parent.parent / "pyvenv.cfg"
    try:
        return f"{python}:{config.stat().st_mtime_ns}"
    except OSError:
        return python


def submit(conn: socket.socket, argv: list[str]) -> dict | None:
    """Send one request, with our own stdio, and wait for the verdict.

    Passing the file descriptors means the command writes straight to ninja's
    pipe: nothing copies its output, and stdout and stderr keep their order.
    """
    request = {
        "argv": argv,
        "cwd": os.getcwd(),
        "env": dict(os.environ),
        "stamp": environment_stamp(sys.executable),
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
    separator = argv.index("--")
    sock_path = Path(argv[0])
    modules, idle_timeout = argv[1], argv[2]
    command = argv[separator + 1 :]

    if os.name == "nt" or not hasattr(socket, "AF_UNIX"):
        return run_directly(command)

    conn = connect(sock_path)
    if conn is None:
        started = spawn(sock_path, modules, idle_timeout)
        import time

        conn = wait_for(sock_path, time.monotonic() + STARTUP_TIMEOUT, started)
    if conn is None:
        return run_directly(command)

    with conn:
        answer = submit(conn, command)

    if answer is None or "exit" not in answer:
        # Refused (a stale worker, or one that cannot fork), or died mid-run.
        # Either way the command has not run, so run it.
        return run_directly(command)
    exit_code = answer["exit"]
    return exit_code if isinstance(exit_code, int) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
