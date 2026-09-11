# SPDX-License-Identifier: MIT
"""Shared helpers for the Command path tests.

The platform names the files and the shell decides how to spell them: a
program is ``gen`` under a POSIX shell and ``gen.exe`` under cmd.exe, and
a relative path needs a ``./`` on one and backslashes on the other. Both
answers come off the resolved target and off the same functions the
generators use, so one assertion holds on every platform.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from pcons.configure.platform import get_platform
from pcons.core.paths import executable_form
from pcons.core.subst import to_shell_command

if TYPE_CHECKING:
    from pcons.core.project import Project
    from pcons.core.target import Target


def built_path(project: Project, target: Target) -> str:
    """The path a generator writes for *target*'s single output.

    @param project Resolved project owning *target*.
    @param target Target whose one output file is wanted.
    @return The output path as the build directory sees it.
    """
    return project._path_resolver.make_execution_relative(target.output_nodes[0].path)


def runs_as(path: str) -> str:
    """Spell *path* the way the shell running this build will execute it.

    @param path A path already relative to the build directory.
    @return The same path in executable form for the host platform.
    """
    return executable_form(path, windows=get_platform().is_windows)


def as_ninja_command(*tokens: str) -> str:
    """Render *tokens* as the ninja generator writes a command line.

    Windows spellings carry backslashes, which the quoting layer then wraps;
    asking the generator's own function keeps the expectation free of both.

    @param tokens The command tokens, already in the form each needs.
    @return The quoted, space-joined command line.
    """
    return to_shell_command(list(tokens), shell="ninja")


def file_name_in(token: str) -> str:
    """The file name inside a recipe token, past separators and quoting.

    @param token The first word of a make recipe line.
    @return Its last path component, with no surrounding quotes.
    """
    return PurePosixPath(token.strip("\"'").replace("\\", "/")).name
