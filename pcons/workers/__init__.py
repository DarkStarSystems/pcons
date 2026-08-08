# SPDX-License-Identifier: MIT
"""Persistent workers: run an action in a process that is already started.

pcons implements no workers. It defines what one must do — ``docs/worker
-protocol.md``, which this package is the client side of — and a project
brings whichever kind suits it. :mod:`pcons.workers.python` is one, bundled
because Python actions are common and because it doubles as a worked example
of the contract.

    env.Command(..., worker=Worker(command=["my-worker", "--profile=render"]))
"""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["Worker", "socket_dir"]

#: Where a worker's socket lives. AF_UNIX caps the whole path at around 104
#: bytes, which a build directory nested a few levels down blows through on
#: its own, so sockets are named by hash under a short directory of our own.
_SOCKET_DIR_NAME = "pcons-workers"


@dataclass(frozen=True)
class Worker:
    """A process to hand an action to, and how to start one.

    Args:
        command: How to start a worker; the socket path is appended to it.
        key: Extra identity material. Two workers with the same start command
            share one process, which is usually what you want -- add
            something here to keep them apart, such as a version whose change
            should not be served by an already-running worker.
        idle_timeout: Seconds a worker should wait for work before exiting.
            Passed on to it in ``PCONS_WORKER_IDLE_TIMEOUT``, since only the
            worker can decide what to do about it.
    """

    command: Sequence[str] = ()
    key: Sequence[str] = ()
    idle_timeout: float = 900.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "command", tuple(self.command))
        object.__setattr__(self, "key", tuple(self.key))
        if not self.command:
            raise ValueError("Worker(command=...) needs a command to start a worker")

    @property
    def identity(self) -> str:
        """What makes two workers the same one: how they start, and *key*."""
        material = "\0".join([*self.command, "\0key\0", *self.key])
        return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]

    @property
    def socket_path(self) -> Path:
        """The socket this worker listens on, the same for every build."""
        return socket_dir() / f"{self.identity}.sock"

    def launcher(self) -> list[str]:
        """Command tokens that route an action through this worker.

        The layout is annotated in ``docs/worker-protocol.md``; the count
        delimits the start command so that one containing ``--`` cannot be
        misread as the end of it.
        """
        client = Path(__file__).parent / "client.py"
        return [
            sys.executable,
            str(client),
            str(self.socket_path),
            str(self.idle_timeout),
            str(len(self.command)),
            *self.command,
            "--",
        ]


def socket_dir() -> Path:
    """The per-user directory holding worker sockets, created if needed.

    Kept short, because AF_UNIX addresses are, and private, because a socket
    that runs commands is nobody else's business.
    """
    base = Path("/tmp") if os.name != "nt" else Path(tempfile.gettempdir())
    suffix = os.getuid() if os.name != "nt" else os.environ.get("USERNAME", "")
    directory = base / f"{_SOCKET_DIR_NAME}-{suffix}"
    directory.mkdir(mode=0o700, exist_ok=True)
    return directory
