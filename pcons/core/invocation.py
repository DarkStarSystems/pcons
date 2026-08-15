# SPDX-License-Identifier: MIT
"""How this pcons run was invoked, so generated build files can re-run it.

Build generators emit a self-regeneration rule (Ninja's ``generator = 1``,
Make's makefile-remake rule) that re-runs pcons when the build script — or
anything it read while describing the build — changes. That needs a faithful
command to re-invoke with, which only the entry point knows: the CLI records
it here, and a directly-run script is inferred from ``sys.argv``.

The reconstructed command always goes through ``python -m pcons generate``,
which runs the build script the same way a direct ``python pcons-build.py``
does — with the same interpreter, so pcons is importable by construction.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

#: ``__name__`` for a build script pcons is running, whether it is the one
#: named on the command line or a subproject pulled in by add_subdirectory.
#: Not "__main__", which belongs to the program: a script that hands over to
#: the CLI from a ``__main__`` guard would otherwise re-enter it forever.
RUN_NAME = "__pcons__"

_current: Invocation | None = None


@dataclass
class Invocation:
    """The command that produced this run, in reconstructable form.

    Attributes:
        script: Path to the build script that was executed.
        variables: ``KEY=value`` build variables.
        variant: ``--variant`` value, if any.
        generators: ``-G`` generator names, if any.

    The build directory is deliberately not recorded: it is *run_dir* in
    :meth:`command`, the directory the build file was actually written to.
    A script is free to choose its own (``Project(build_dir=...)``), and only
    the generator knows where the file it is writing ended up.
    """

    script: Path
    variables: dict[str, str] = field(default_factory=dict)
    variant: str | None = None
    generators: list[str] = field(default_factory=list)

    def command(self, *, root_dir: Path, run_dir: Path) -> list[str] | None:
        """The argv that re-runs pcons, to be executed from *run_dir*.

        Paths are expressed relative to *run_dir* (the build directory, where
        both Ninja and Make run recipes) so generated build files stay
        relocatable. Returns None when the script can't be found.
        """
        script = self.script if self.script.is_absolute() else root_dir / self.script
        if not script.is_file():
            return None

        try:
            topdir = os.path.relpath(root_dir, run_dir)
        except ValueError:
            # Different drives on Windows: an absolute path is all we have.
            topdir = str(root_dir)

        # Forward slashes throughout: the command text passes through ninja
        # and make, both of which treat backslashes as escapes, and Python's
        # chdir/open accept them on Windows.
        argv = [
            sys.executable.replace("\\", "/"),
            "-m",
            "pcons",
            "generate",
            "-C",
            topdir.replace("\\", "/"),
            "-B",
            _rel_to(run_dir, root_dir),
            "-b",
            _rel_to(script, root_dir),
        ]
        if self.variant:
            argv += ["--variant", self.variant]
        for gen in self.generators:
            argv += ["-G", gen]
        argv += [f"{key}={value}" for key, value in sorted(self.variables.items())]
        # A regen re-invoke is self-contained from this argv; it must not write
        # its own cache file into the build dir it regenerates. See PR #64.
        argv.append("--no-cache")
        return argv


def _rel_to(path: Path, root_dir: Path) -> str:
    """*path* relative to *root_dir* when possible, else absolute."""
    try:
        return str(path.relative_to(root_dir)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def record(invocation: Invocation) -> None:
    """Record how this run was invoked (called by the CLI)."""
    global _current
    _current = invocation


def running_as_a_program(defined_at: Path) -> bool:
    """Whether *defined_at* is the file this interpreter was started on.

    True for ``python pcons-build.py`` and false for anything pcons ran: the
    CLI records an invocation before it executes a build script, so the script
    named on the command line and every one add_subdirectory pulls in are ruled
    out by the recorded invocation alone.

    An embedder's own driver run as ``python driver.py`` matches too. Nothing
    at construction time separates it from a build script, and the cost is one
    warning on a build that still works.
    """
    if _current is not None:
        return False
    program = sys.argv[0] if sys.argv else ""
    if not program:
        return False
    try:
        return defined_at.resolve() == Path(program).resolve()
    except (OSError, ValueError):  # pragma: no cover - unresolvable, not ours
        return False


def program_name(path: Path) -> str:
    """*path* spelled relative to the working directory when it can be.

    A diagnostic naming the program should name it the way the user typed it,
    and an absolute path is what a frame carries.
    """
    try:
        return os.path.relpath(path)
    except ValueError:  # pragma: no cover - another drive on Windows
        return str(path)


def clear() -> None:
    """Forget the recorded invocation (between CLI runs, and in tests)."""
    global _current
    _current = None


def current() -> Invocation | None:
    """The recorded invocation, or one inferred from a direct script run.

    Inference covers ``python pcons-build.py``, where no CLI ran to record
    anything: ``sys.argv[0]`` is the build script.
    """
    if _current is not None:
        return _current

    script = Path(sys.argv[0]) if sys.argv and sys.argv[0] else None
    if script is None or script.suffix != ".py" or not script.is_file():
        return None
    return Invocation(script=script.resolve())
