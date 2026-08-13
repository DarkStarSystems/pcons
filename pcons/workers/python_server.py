# SPDX-License-Identifier: MIT
"""A worker for Python actions, and a worked example of the contract.

    python python_server.py [--preload m1,m2] [--setup pkg.mod:fn] <socket>

Implements ``docs/worker-protocol.md``; read this beside it if you are writing
a worker of your own. Isolation, which the contract requires and leaves to the
worker, comes from forking a child per request here.

Standard library only, and never imports pcons: whatever this process holds is
inherited by every action it runs.
"""

from __future__ import annotations

# argparse, not click: this runs under the interpreter passed to
# Worker(python=...), which is the project's and need not have pcons, or
# click, installed.
import argparse
import array
import json
import os
import runpy
import socket
import subprocess
import sys
import threading
from pathlib import Path

REPLY_STALE = "stale"
REPLY_UNSUPPORTED = "unsupported"


def become_ready(modules: list[str], setup: str) -> None:
    """Do the expensive work once, in the parent, before any action runs.

    Importing is the cheap case to express; *setup* is the general one --
    ``package.module:function``, called with no arguments, free to open
    whatever the actions to come will need.
    """
    for name in modules:
        __import__(name)
    if setup:
        module_name, _, attribute = setup.partition(":")
        module = __import__(module_name, fromlist=["*"])
        getattr(module, attribute)()


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


def os_thread_count() -> int | None:
    """Threads in this process as the operating system counts them.

    ``threading.active_count()`` sees only Python threads, and the hazard that
    makes forking unsafe is native ones -- OpenMP, TBB, a threaded BLAS --
    which numpy, scipy and scikit-learn routinely start while being imported.
    Asked once, after becoming ready, since that is when they would appear.

    Returns None when it cannot be determined, which is not the same as one.
    """
    try:
        with open("/proc/self/status", encoding="utf-8") as status:  # Linux
            for line in status:
                if line.startswith("Threads:"):
                    return int(line.split()[1])
    except OSError:
        pass
    try:  # macOS and the BSDs: one line per thread, after a header
        listing = subprocess.run(
            ["ps", "-M", str(os.getpid())],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if listing.returncode == 0:
            rows = [row for row in listing.stdout.splitlines()[1:] if row.strip()]
            return len(rows) or None
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def fork_hazard() -> str:
    """Why forking would be unsafe here, or "" when it is safe.

    Erring towards refusing: an unknown thread count is treated as a reason to
    stand aside, because the cost of being wrong is a corrupted child rather
    than a slow build.
    """
    if threading.active_count() != 1:
        return "the worker has started Python threads, so forking is unsafe"
    native = os_thread_count()
    if native is None:
        return "the worker cannot count its own threads, so forking may be unsafe"
    if native > 1:
        return f"the worker has {native} native threads, so forking is unsafe"
    return ""


def debug(message: str) -> None:
    """Explain a refusal. Discarded unless the client asked to hear it."""
    if os.environ.get("PCONS_WORKER_DEBUG"):
        print(f"pcons worker: {message}", file=sys.stderr, flush=True)


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


def serve(sock_path: Path, modules: list[str], setup: str, idle_timeout: float) -> int:
    """Listen until nothing has asked for work in *idle_timeout* seconds."""
    become_ready(modules, setup)

    # Asked once, now, rather than per request: threads appear while becoming
    # ready, and asking the operating system is not free.
    hazard = fork_hazard()
    if hazard:
        debug(hazard)
    # The environment served is adopted from the first client, which stamps
    # the interpreter it asked to have started -- so both sides agree on it
    # even when pcons itself is running from somewhere else entirely.
    state: dict = {"stamp": None, "hazard": hazard}

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
            _serve_one(server, conn, state)
            _reap()
    finally:
        server.close()
        # Only if it is still ours: a newer worker may have renamed over it.
        sock_path.unlink(missing_ok=True)


def _refusal(request: dict, state: dict) -> str:
    """Why this worker will not run the request, or "" when it will.

    Refusing costs a build the worker's speed. Approximating costs it its
    correctness, so anything not exactly right is refused.
    """
    if state["hazard"]:
        return state["hazard"]

    argv = list(request.get("argv", []))
    runnable = script_argv(argv)
    if runnable is None:
        return f"not an interpreter running a script: {' '.join(argv[:2])}"

    # Running the script in *this* interpreter is only right if it is the one
    # the action asked for. A different venv would import different packages.
    wanted = venv_of(argv[0])
    if wanted != venv_of(sys.executable):
        return f"the action wants {wanted}, this worker is {venv_of(sys.executable)}"

    request["runnable"] = runnable
    return ""


def _serve_one(server: socket.socket, conn: socket.socket, state: dict) -> None:
    """Fork a child to handle one connection, or refuse it with a reason."""
    with conn:
        try:
            request, fds = recv_request(conn)
        except (OSError, ValueError):
            return

        stamp = request.get("stamp")
        if state["stamp"] is None:
            # Adopt the first environment described to us. A worker need not
            # know how a stamp is made -- only that a different one means the
            # environment it holds in memory is no longer the one being built.
            state["stamp"] = stamp
        elif stamp != state["stamp"]:
            reply(conn, error="the environment changed under this worker")
            debug("environment changed; standing down")
            raise SystemExit(0)

        refusal = _refusal(request, state)
        if refusal:
            reply(conn, error=refusal)
            debug(f"refused: {refusal}")
            for fd in fds:
                os.close(fd)
            return

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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--preload", default="", help="modules to import, comma-separated"
    )
    parser.add_argument("--setup", default="", help="package.module:function to call")
    parser.add_argument("socket", help="where to listen; pcons appends this")
    args = parser.parse_args(argv)

    # The client passes the timeout in the environment rather than in the
    # command, so that a worker's own arguments stay its own business.
    idle_timeout = float(os.environ.get("PCONS_WORKER_IDLE_TIMEOUT", "900"))
    modules = [m for m in args.preload.split(",") if m]
    return serve(Path(args.socket), modules, args.setup, idle_timeout)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
