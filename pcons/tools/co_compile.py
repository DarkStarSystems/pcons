# SPDX-License-Identifier: MIT
"""Run an analyzer alongside each compile, then compile.

A pcons launcher prepends tokens to a command and relies on the launcher
running whatever argv is left, the way ``ccache clang++ -c foo.cc`` works.
clang-tidy cannot be one: it analyzes and produces no object file, and its
arguments are shaped ``clang-tidy [opts] <source> -- <compile flags>``. So
this module is the launcher, and runs both. ``env.use_clang_tidy()`` sets it
up; by hand it is::

    env.cxx.launcher = [
        sys.executable, "-m", "pcons.tools.co_compile",
        "--tidy", "clang-tidy", "--tidy-arg", "--use-color", "--",
    ]

CMake spells the same idea ``cmake -E __run_co_compile --tidy=...``, which is
what its ``CXX_CLANG_TIDY`` target property drives.

The analyzer never sees ``-o`` or the depfile flags, so it cannot argue about
an output it will not write or clobber the ``.d`` the real compile is about to
produce. A launcher that runs inside this one (a compiler cache, say) stays in
front of the compile and out of the analysis. The compile runs even when the
analysis complained, so the object still appears and the diagnostics repeat
on the next build rather than turning into a missing-file error somewhere
downstream. Exits nonzero if either half failed, so a diagnostic fails the
build the way CMake's does.

``compile_commands.json`` is unaffected: pcons reports the compiler itself,
without launchers, so clangd sees the real compile.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pcons.core.environment import Environment

logger = logging.getLogger("pcons")

SOURCE_SUFFIXES = (".cc", ".cpp", ".cxx", ".c++", ".C", ".c", ".m", ".mm")

#: Flags that must not reach the analyzer, and consume the argument after them.
DROP_WITH_ARG = ("-o", "-MF", "-MT", "-MQ")
#: The same, but standing alone.
DROP_ALONE = ("-c", "-MD", "-MMD", "-MP")

#: Tools clang-tidy can analyze: the C and C++ compiles.
ANALYZED_TOOLS = ("cc", "cxx")

#: The launcher tokens up to the analyzer's own options.
_LAUNCHER_HEAD = (sys.executable, "-m", "pcons.tools.co_compile")


def apply_clang_tidy(
    env: Environment, tool: str | None = None, args: Sequence[str] = ()
) -> None:
    """Run clang-tidy alongside every C and C++ compile of *env*.

    A missing clang-tidy is a warning rather than an error, so a build
    still works on a machine without one. This launcher goes outermost: a
    compiler cache set before or after it keeps wrapping the compiler.
    """
    program = shutil.which(tool or "clang-tidy")
    if program is None:
        logger.warning("clang-tidy not found (looked for '%s')", tool or "clang-tidy")
        return

    tidy_args = [arg for a in args for arg in ("--tidy-arg", a)]
    launcher = [*_LAUNCHER_HEAD, "--tidy", program, *tidy_args, "--"]
    for tool_name in ANALYZED_TOOLS:
        if not env.has_tool(tool_name):
            continue
        tool_config = getattr(env, tool_name)
        if _LAUNCHER_HEAD[-1] in tool_config.launcher:
            continue
        tool_config.launcher = [*launcher, *tool_config.launcher]


def split_argv(argv: list[str]) -> tuple[str, list[str], list[str]]:
    """Parse this module's own options up to ``--``; the rest is the command."""
    tidy = ""
    tidy_args: list[str] = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--":
            return tidy, tidy_args, argv[i + 1 :]
        if arg == "--tidy":
            tidy = argv[i + 1]
            i += 2
            continue
        if arg == "--tidy-arg":
            tidy_args.append(argv[i + 1])
            i += 2
            continue
        raise SystemExit(f"co_compile: unknown option {arg!r}")
    raise SystemExit("co_compile: missing `--` before the compile command")


def analyzer_command(
    tidy: str, tidy_args: list[str], compile_cmd: list[str]
) -> list[str] | None:
    """``clang-tidy <opts> <source> -- <flags>``, or None when there is no source.

    The compiler is the last token before the first flag or source; anything
    ahead of it is a launcher wrapping the compile, which the analysis has no
    use for. After the compiler come the flags, save the sources and the few
    flags that would collide with the real compile.
    """
    sources = [a for a in compile_cmd if a.endswith(SOURCE_SUFFIXES)]
    if not sources:
        return None

    first_flag = next(
        i for i, a in enumerate(compile_cmd) if a.startswith("-") or a in sources
    )
    flags: list[str] = []
    skip = False
    for arg in compile_cmd[max(first_flag, 1) :]:
        if skip:
            skip = False
            continue
        if arg in DROP_WITH_ARG:
            skip = True
            continue
        if arg in DROP_ALONE or arg in sources:
            continue
        flags.append(arg)

    return [tidy, *tidy_args, *sources, "--", *flags]


def main(argv: list[str] | None = None) -> int:
    """Analyze, then compile. Returns the first nonzero status of the two."""
    tidy, tidy_args, compile_cmd = split_argv(sys.argv[1:] if argv is None else argv)
    if not compile_cmd:
        raise SystemExit("co_compile: empty compile command")

    analysis = 0
    if tidy:
        cmd = analyzer_command(tidy, tidy_args, compile_cmd)
        if cmd is not None:
            analysis = subprocess.run(cmd).returncode

    compiled = subprocess.run(compile_cmd).returncode
    return compiled or analysis


if __name__ == "__main__":
    sys.exit(main())
