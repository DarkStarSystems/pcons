# SPDX-License-Identifier: MIT
"""Run analyzers alongside each compile, then compile.

A pcons launcher prepends tokens to a command and relies on the launcher
running whatever argv is left, the way ``ccache clang++ -c foo.cc`` works.
An analyzer cannot be one: it reads the source with the compile's flags and
produces no object file. clang-tidy takes them as
``clang-tidy [opts] <source> -- <compile flags>``, include-what-you-use as
``include-what-you-use [opts] <compile flags> <source>``. So this module is
the launcher: it runs each analyzer it was given, then the compile.
``env.use_clang_tidy()`` and ``env.use_include_what_you_use()`` set it up; by
hand it is::

    env.cxx.launcher = [
        sys.executable, "-m", "pcons.tools.co_compile",
        "--tidy", "clang-tidy", "--tidy-arg", "--use-color", "--",
    ]

CMake spells the same idea ``cmake -E __run_co_compile --tidy=... --iwyu=...``,
which its ``CXX_CLANG_TIDY`` and ``CXX_INCLUDE_WHAT_YOU_USE`` target
properties drive.

The analyzer never sees ``-o`` or the depfile flags, so it cannot argue about
an output it will not write or clobber the ``.d`` the real compile is about to
produce. A launcher that runs inside this one (a compiler cache, say) stays in
front of the compile and out of the analysis. The compile runs even when the
analysis complained, so the object still appears and the diagnostics repeat
on the next build rather than turning into a missing-file error somewhere
downstream. Exits nonzero if the compile or clang-tidy failed: clang-tidy
exits zero on a warning, so only an error, or a warning under
``--warnings-as-errors``, fails the build, the way CMake's does.
include-what-you-use only reports, unless given its own ``--error`` option:
older versions exit nonzero whatever they found, which CMake ignores too.

``compile_commands.json`` is unaffected: pcons reports the compiler itself,
without launchers, so clangd sees the real compile.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pcons.core.environment import Environment

logger = logging.getLogger("pcons")

SOURCE_SUFFIXES = (".cc", ".cpp", ".cxx", ".c++", ".C", ".c", ".m", ".mm")

#: Flags that must not reach the analyzer, and consume the argument after them.
DROP_WITH_ARG = ("-o", "-MF", "-MT", "-MQ")
#: The same, but standing alone.
DROP_ALONE = ("-c", "-MD", "-MMD", "-MP", "/c", "/showIncludes")
#: MSVC-style flags that carry their argument joined: the outputs.
DROP_PREFIXED = ("/Fo", "/Fd", "/Fe", "/Fi")
#: Compilers that take MSVC-style flags; the analyzers read them in cl mode.
CL_COMPILERS = ("cl", "clang-cl")

#: Tools the analyzers can analyze: the C and C++ compiles.
ANALYZED_TOOLS = ("cc", "cxx")

#: The launcher tokens up to the analyzers' own options.
_LAUNCHER_HEAD = (sys.executable, "-m", "pcons.tools.co_compile")


def apply_clang_tidy(
    env: Environment, tool: str | None = None, args: Sequence[str] = ()
) -> None:
    """Run clang-tidy alongside every C and C++ compile of *env*."""
    _apply_analyzer(env, "clang-tidy", "--tidy", tool, args)


def apply_include_what_you_use(
    env: Environment, tool: str | None = None, args: Sequence[str] = ()
) -> None:
    """Run include-what-you-use alongside every C and C++ compile of *env*."""
    _apply_analyzer(env, "include-what-you-use", "--iwyu", tool, args)


def _apply_analyzer(
    env: Environment,
    default: str,
    option: str,
    tool: str | None,
    args: Sequence[str],
) -> None:
    """Add an analyzer to *env*'s C and C++ compiles.

    A missing analyzer is a warning rather than an error, so a build still
    works on a machine without one. This launcher goes outermost: a compiler
    cache set before or after it keeps wrapping the compiler. A second
    analyzer joins the launcher the first one set up, ahead of its ``--``.
    """
    program = shutil.which(tool or default)
    if program is None:
        logger.warning("%s not found (looked for '%s')", default, tool or default)
        return

    options = [option, program, *(a for arg in args for a in (f"{option}-arg", arg))]
    for tool_name in ANALYZED_TOOLS:
        if not env.has_tool(tool_name):
            continue
        tool_config = getattr(env, tool_name)
        launcher = list(tool_config.launcher)
        if _LAUNCHER_HEAD[-1] not in launcher:
            tool_config.launcher = [*_LAUNCHER_HEAD, *options, "--", *launcher]
            continue
        if option in launcher:
            continue
        end = launcher.index("--", launcher.index(_LAUNCHER_HEAD[-1]))
        tool_config.launcher = [*launcher[:end], *options, *launcher[end:]]


@dataclass
class Analyzers:
    """The analyzers one compile runs, with their own arguments."""

    tidy: str = ""
    tidy_args: list[str] = field(default_factory=list)
    iwyu: str = ""
    iwyu_args: list[str] = field(default_factory=list)


def split_argv(argv: list[str]) -> tuple[Analyzers, list[str]]:
    """Parse this module's own options up to ``--``; the rest is the command."""
    analyzers = Analyzers()
    values = {
        "--tidy": "tidy",
        "--iwyu": "iwyu",
    }
    lists = {
        "--tidy-arg": analyzers.tidy_args,
        "--iwyu-arg": analyzers.iwyu_args,
    }
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--":
            return analyzers, argv[i + 1 :]
        if arg in values:
            setattr(analyzers, values[arg], argv[i + 1])
        elif arg in lists:
            lists[arg].append(argv[i + 1])
        else:
            raise SystemExit(f"co_compile: unknown option {arg!r}")
        i += 2
    raise SystemExit("co_compile: missing `--` before the compile command")


def _compile_parts(compile_cmd: list[str]) -> tuple[list[str], list[str], bool] | None:
    """The compile's sources, the flags an analyzer gets, and whether they
    are cl.exe's, or None when there is no source.

    The compiler is the last token before the first flag or source; anything
    ahead of it is a launcher wrapping the compile, which the analysis has no
    use for. After the compiler come the flags, save the sources and the few
    flags that would collide with the real compile.
    """
    sources = [a for a in compile_cmd if a.endswith(SOURCE_SUFFIXES)]
    if not sources:
        return None

    first_flag = next(
        i for i, a in enumerate(compile_cmd) if a.startswith(("-", "/")) or a in sources
    )
    compiler = Path(compile_cmd[max(first_flag, 1) - 1]).stem.lower()
    flags: list[str] = []
    skip = False
    for arg in compile_cmd[max(first_flag, 1) :]:
        if skip:
            skip = False
            continue
        if arg in DROP_WITH_ARG:
            skip = True
            continue
        if arg in DROP_ALONE or arg in sources or arg.startswith(DROP_PREFIXED):
            continue
        flags.append(arg)
    return sources, flags, compiler in CL_COMPILERS


def tidy_command(
    tidy: str, tidy_args: list[str], compile_cmd: list[str]
) -> list[str] | None:
    """``clang-tidy <opts> <source> -- <flags>``, or None when there is no source.

    cl.exe's flags mean nothing to clang's GNU driver: clang-tidy parses the
    compile line as clang-cl would, which is how CMake does it too.
    """
    parts = _compile_parts(compile_cmd)
    if parts is None:
        return None
    sources, flags, cl_mode = parts
    mode = ["--extra-arg-before=--driver-mode=cl"] if cl_mode else []
    return [tidy, *tidy_args, *mode, *sources, "--", *flags]


def iwyu_command(
    iwyu: str, iwyu_args: list[str], compile_cmd: list[str]
) -> list[str] | None:
    """``include-what-you-use <opts> <flags> <source>``, or None when there
    is no source. It takes a compile line as clang does, in cl mode for
    cl.exe's flags."""
    parts = _compile_parts(compile_cmd)
    if parts is None:
        return None
    sources, flags, cl_mode = parts
    mode = ["--driver-mode=cl"] if cl_mode else []
    return [iwyu, *mode, *iwyu_args, *flags, *sources]


def main(argv: list[str] | None = None) -> int:
    """Analyze, then compile. Returns the first nonzero status that counts."""
    analyzers, compile_cmd = split_argv(sys.argv[1:] if argv is None else argv)
    if not compile_cmd:
        raise SystemExit("co_compile: empty compile command")

    analysis = 0
    if analyzers.tidy:
        cmd = tidy_command(analyzers.tidy, analyzers.tidy_args, compile_cmd)
        if cmd is not None:
            analysis = subprocess.run(cmd).returncode
    if analyzers.iwyu:
        cmd = iwyu_command(analyzers.iwyu, analyzers.iwyu_args, compile_cmd)
        if cmd is not None:
            status = subprocess.run(cmd).returncode
            if any(arg.startswith("--error") for arg in analyzers.iwyu_args):
                analysis = analysis or status

    compiled = subprocess.run(compile_cmd).returncode
    return compiled or analysis


if __name__ == "__main__":
    sys.exit(main())
