# SPDX-License-Identifier: MIT
"""Persistent workers: pay an action's startup cost once, not every build.

Ninja assumes starting a command is free. For ``cc`` it is. For an action that
must become *ready* before it can do anything — load a large library, connect
to a remote service, claim a licence, warm a cache — startup can dominate the
build, and it is paid again on every edit.

A worker is a process kept alive across actions, so whatever it takes to
become ready is paid once. It then forks a pristine child per request, so
every action still runs in a process that has never seen another action's
state. Ninja cannot start the worker itself — it would get a fresh one per
action, which is the thing being avoided — so it runs a small client that
hands the work to a worker, starting one if none is listening.

Workers are declared per command:

    env.Command(
        target="report.pdf",
        source="report.py",
        command="python $SOURCE --out $TARGET",
        worker=Worker(preload=["heavy_toolkit"]),
    )

This worker becomes ready by importing Python modules, which is the form the
readiness takes here; the client, the protocol and the fork-per-request
arrangement care about none of that.

Fork is what keeps it honest. A worker that reset itself between actions
instead would carry the previous one's state — a cache populated before an
edit, a connection holding a stale handle — and quietly build the wrong thing.
"""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["Worker", "socket_dir"]

#: Where a worker's socket lives. AF_UNIX caps the whole path at around 104
#: bytes, which a build directory nested a few levels down blows through on
#: its own, so it is keyed by a hash under a short directory of our choosing.
_SOCKET_DIR_NAME = "pcons-workers"


@dataclass(frozen=True)
class Worker:
    """A ready-to-run process to hand a command to.

    Args:
        preload: What this worker does to become ready: Python modules it
            imports before forking. Installed packages only -- never a module
            of the project being built, whose whole purpose is to change
            between builds. The parent holds the preloaded state; the child
            loads the command's own code fresh, which is what keeps an edit
            from being masked by what the parent already has.
        python: Interpreter to run, defaulting to the one running pcons.
        idle_timeout: Seconds a worker waits for work before exiting, so a
            forgotten one does not outlive the session by much.
    """

    preload: tuple[str, ...] = field(default_factory=tuple)
    python: str = ""
    idle_timeout: float = 900.0

    def __post_init__(self) -> None:
        # Accept a list and a bare string, both of which read naturally.
        preload = self.preload
        if isinstance(preload, str):
            object.__setattr__(self, "preload", (preload,))
        else:
            object.__setattr__(self, "preload", tuple(preload))
        if not self.python:
            object.__setattr__(self, "python", sys.executable)

    @property
    def identity(self) -> str:
        """What makes two workers the same one: interpreter and preload set.

        Everything a worker holds comes from these, so anything else sharing
        them can share the worker.
        """
        material = "\0".join([self.python, *sorted(self.preload)])
        return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]

    @property
    def socket_path(self) -> Path:
        """The socket this worker listens on, the same for every build."""
        return socket_dir() / f"{self.identity}.sock"

    def launcher(self) -> list[str]:
        """Command tokens that route an action through this worker.

        The client execs the command directly when no worker answers, so a
        generated build file still builds by itself -- with plain ninja, or in
        CI, where nothing started a worker.
        """
        client = Path(__file__).parent / "client.py"
        return [
            self.python,
            str(client),
            str(self.socket_path),
            ",".join(sorted(self.preload)),
            str(self.idle_timeout),
            "--",
        ]


def socket_dir() -> Path:
    """The per-user directory holding worker sockets, created if needed.

    Kept short, because AF_UNIX paths are, and private, because a socket that
    runs commands is not something to share with the rest of the machine.
    """
    base = Path("/tmp") if os.name != "nt" else Path(tempfile.gettempdir())
    directory = base / f"{_SOCKET_DIR_NAME}-{os.getuid() if os.name != 'nt' else ''}"
    directory.mkdir(mode=0o700, exist_ok=True)
    return directory
