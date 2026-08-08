# SPDX-License-Identifier: MIT
"""The worker process: become ready once, fork a pristine child per request.

    python server.py <socket> <comma-separated modules> <idle timeout>

Started by the client when no worker is listening (see client.py); nothing
else knows about it. Standard library only, and never imports pcons: whatever
this process holds is inherited by every action it runs.

Becoming ready here means importing the modules it was asked to preload. That
is one way for a process to be expensive to start; the arrangement around it --
one long-lived parent, a fresh child per action -- suits any of them.

Each request is served by a forked child that owns the connection and reports
its own exit status, so requests are concurrent and a child that dies takes
nothing with it. The client passes its own stdout and stderr as file
descriptors, which the child writes to directly -- output reaches ninja in the
order the program produced it, with nothing relaying or reordering it.
"""

from __future__ import annotations

import array
import json
import os
import runpy
import socket
import sys
import threading
from pathlib import Path

REPLY_STALE = "stale"
REPLY_UNSUPPORTED = "unsupported"


def preload(modules: list[str]) -> None:
    """Do the expensive setup once, in the parent, before any action runs."""
    for name in modules:
        __import__(name)


def environment_stamp(python: str) -> str:
    """A cheap fingerprint of the interpreter's environment.

    A worker holds library code in memory, so a changed virtualenv makes it
    stale. Comparing this on every request costs one stat and turns "the
    build used the old library" into a restart.
    """
    config = Path(python).resolve().parent.parent / "pyvenv.cfg"
    try:
        return f"{python}:{config.stat().st_mtime_ns}"
    except OSError:
        return python


def recv_request(conn: socket.socket) -> tuple[dict, list[int]]:
    """Read one request and the file descriptors that came with it."""
    fds = array.array("i")
    message, ancillary, _flags, _addr = conn.recvmsg(
        65536, socket.CMSG_SPACE(3 * fds.itemsize)
    )
    for level, kind, data in ancillary:
        if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
            fds.frombytes(data[: len(data) - (len(data) % fds.itemsize)])
    return json.loads(message.decode("utf-8")), list(fds)


def reply(conn: socket.socket, **fields: object) -> None:
    """Answer the client. One JSON line, out of band from the command's output."""
    try:
        conn.sendall((json.dumps(fields) + "\n").encode("utf-8"))
    except OSError:
        pass  # the client gave up; nothing useful left to say


def script_argv(argv: list[str]) -> list[str] | None:
    """The script and its arguments, or None if this is not ours to run.

    A worker can only stand in for a command it can run *in* itself: an
    interpreter invoking a script. Anything else -- another program, or an
    interpreter given something other than a script to run -- is handed back
    for the client to run directly, rather than approximated here.
    """
    if len(argv) < 2 or "python" not in Path(argv[0]).name.lower():
        return None
    if argv[1].startswith("-"):
        return None  # -c, -m, and flags we would have to reimplement
    return argv[1:]


def run_request(request: dict, fds: list[int]) -> int:
    """Run one action in this (forked) process and return its exit status."""
    for target, fd in enumerate(fds[:3]):
        os.dup2(fd, target)
    for fd in fds:
        os.close(fd)

    os.chdir(request["cwd"])
    os.environ.clear()
    os.environ.update(request["env"])

    argv = list(request["runnable"])
    script = argv[0]
    sys.argv = argv
    # `python script.py` puts the script's directory first on sys.path;
    # runpy does not, and the difference shows up as the project's own
    # modules suddenly not importing.
    sys.path.insert(0, str(Path(script).resolve().parent))

    try:
        runpy.run_path(script, run_name="__main__")
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else (1 if e.code else 0)
    except BaseException:  # noqa: BLE001 - the action's failure, not ours
        import traceback

        traceback.print_exc()
        return 1
    return 0


def serve(sock_path: Path, modules: list[str], idle_timeout: float) -> int:
    """Listen until nothing has asked for work in *idle_timeout* seconds."""
    preload(modules)
    stamp = environment_stamp(sys.executable)

    # Bind to a temporary name and rename into place, so a second worker
    # racing to start loses the rename rather than unlinking a live socket.
    tmp_path = sock_path.with_name(f".{sock_path.name}.{os.getpid()}")
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        server.bind(str(tmp_path))
        os.chmod(tmp_path, 0o600)
        server.listen(64)
        os.rename(tmp_path, sock_path)
    except OSError:
        server.close()
        tmp_path.unlink(missing_ok=True)
        return 1

    server.settimeout(idle_timeout)
    try:
        while True:
            try:
                conn, _ = server.accept()
            except TimeoutError:
                return 0
            _serve_one(server, conn, stamp)
            _reap()
    finally:
        server.close()
        # Only if it is still ours: a newer worker may have renamed over it.
        sock_path.unlink(missing_ok=True)


def _serve_one(server: socket.socket, conn: socket.socket, stamp: str) -> None:
    """Fork a child to handle one connection, or refuse it with a reason."""
    with conn:
        try:
            request, fds = recv_request(conn)
        except (OSError, ValueError):
            return

        if request.get("stamp") != stamp:
            # The virtualenv moved under us; the client will start a worker
            # that matches, and this one is of no further use.
            reply(conn, error=REPLY_STALE)
            raise SystemExit(0)

        runnable = script_argv(list(request.get("argv", [])))
        # Forking a threaded process is not safe, and something preloaded here
        # may have started a thread.
        if runnable is None or threading.active_count() != 1:
            reply(conn, error=REPLY_UNSUPPORTED)
            for fd in fds:
                os.close(fd)
            return
        request["runnable"] = runnable

        pid = os.fork()
        if pid == 0:
            server.close()
            code = 1
            try:
                code = run_request(request, fds)
            finally:
                sys.stdout.flush()
                sys.stderr.flush()
                reply(conn, exit=code)
                os._exit(0)
        for fd in fds:
            os.close(fd)


def _reap() -> None:
    """Collect finished children, so a long session does not fill with zombies."""
    try:
        while os.waitpid(-1, os.WNOHANG)[0]:
            pass
    except ChildProcessError:
        pass


def main(argv: list[str]) -> int:
    sock_path, modules_spec, timeout = argv[0], argv[1], argv[2]
    modules = [m for m in modules_spec.split(",") if m]
    return serve(Path(sock_path), modules, float(timeout))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
