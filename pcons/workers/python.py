# SPDX-License-Identifier: MIT
"""A :class:`~pcons.workers.Worker` for actions that run a Python script.

Convenience over :class:`~pcons.workers.Worker`, not a separate mechanism:
this builds the start command for :mod:`pcons.workers.python_server`, which
is one worker among the many a project might bring.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from pcons.workers import Worker

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["PythonWorker"]


def PythonWorker(  # noqa: N802 - a factory, named for what it returns
    *,
    preload: Sequence[str] | str = (),
    setup: str = "",
    python: str = "",
    idle_timeout: float = 900.0,
) -> Worker:
    """A worker holding an already-ready Python interpreter.

    Args:
        preload: Modules to import before any action runs. Installed packages
            only -- never a module of the project being built, which has to
            load fresh or an edit to it would be masked by the copy the worker
            already holds.
        setup: ``package.module:function`` to call once the imports are done,
            for readiness that is not an import: opening a connection,
            claiming a licence, warming a cache.
        python: Interpreter to run, defaulting to the one running pcons.
        idle_timeout: Seconds to wait for work before exiting.

    Returns:
        A Worker that runs Python actions, and nothing else -- a command that
        is not an interpreter running a script is handed back and run
        directly, rather than approximated.
    """
    from pathlib import Path

    if isinstance(preload, str):
        preload = (preload,)
    server = Path(__file__).parent / "python_server.py"
    command = [python or sys.executable, str(server)]
    if preload:
        command += ["--preload", ",".join(sorted(preload))]
    if setup:
        command += ["--setup", setup]
    return Worker(command=command, idle_timeout=idle_timeout)
