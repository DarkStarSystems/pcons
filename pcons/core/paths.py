# SPDX-License-Identifier: MIT
"""Path resolution utilities for consistent output path handling.

PathResolver provides centralized path handling where:
- Target (output) paths are relative to build_dir
- Source (input) paths are relative to project root
- Absolute paths pass through unchanged
- Path and string arguments behave identically
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pcons.util.source_location import SourceLocation

logger = logging.getLogger(__name__)


class PathResolver:
    """Centralized path handling for pcons builds.

    Provides consistent path normalization for both source files (inputs)
    and target files (outputs), ensuring all paths are properly relative
    to their respective base directories.

    Attributes:
        project_root: The root directory of the project.
        build_dir: The build output directory.
    """

    __slots__ = ("project_root", "build_dir", "_resolved_build_dir")

    def __init__(self, project_root: Path, build_dir: Path) -> None:
        self.project_root = project_root.resolve()
        self.build_dir = build_dir
        if build_dir.is_absolute():
            self._resolved_build_dir = build_dir.resolve()
        else:
            self._resolved_build_dir = (self.project_root / build_dir).resolve()

    def subdir(self, subdir: str | Path) -> PathResolver:
        """Return a new PathResolver with project_root and build_dir in *subdir*."""
        return PathResolver(self.project_root / subdir, self.build_dir / subdir)

    def normalize_target_path(
        self,
        path: Path | str,
        *,
        target_name: str | None = None,
        warn_at: SourceLocation | None = None,
        build_dir: Path | None = None,
    ) -> Path:
        """Normalize a target (output) path to be relative to build_dir.

        A relative path that starts with the build_dir prefix has the prefix
        absorbed, once: ``build_dir / "foo.h"`` and ``"foo.h"`` both mean
        ``<build_dir>/foo.h``, so targets may be written from the project
        root or from the build directory interchangeably. A file in a
        literal subdirectory that happens to share the build directory's
        name is written with the prefix twice.

        With *warn_at*, the absorption is announced, blamed on that location.
        Callers pass it for hand-typed string targets, where the text may
        have meant that literal subdirectory; paths built by ``build_dir /``
        arithmetic are unambiguous and stay quiet.

        *build_dir* overrides the base directory (still relative to the
        project root) for callers that anchor somewhere other than this
        resolver's own build_dir, e.g. a sub-project's offset directory or an
        environment with a ``build_prefix``. Absorption then accepts either
        that base or this resolver's own, so ``project.build_dir / "x.h"``
        and ``"x.h"`` keep meaning the same file.
        """
        if build_dir is None:
            bd, resolved_bd = self.build_dir, self._resolved_build_dir
        elif build_dir.is_absolute():
            bd, resolved_bd = build_dir, build_dir.resolve()
        else:
            bd, resolved_bd = build_dir, (self.project_root / build_dir).resolve()

        path_str = str(path).replace("\\", "/")
        path_obj = Path(path_str)

        if path_obj.is_absolute():
            try:
                return path_obj.relative_to(resolved_bd)
            except ValueError:
                # Not under build_dir - external output
                return path_obj

        parts = path_obj.parts
        bd_parts: tuple[str, ...] = ()
        for candidate in (bd, self.build_dir):
            cand_parts = candidate.parts
            if cand_parts and parts[: len(cand_parts)] == cand_parts:
                bd_parts = cand_parts
                break
        if bd_parts:
            absorbed = (
                Path(*parts[len(bd_parts) :]) if parts[len(bd_parts) :] else (Path("."))
            )
            # A doubled prefix is the explicit form the warning itself
            # recommends for a literal same-named subdirectory: quiet.
            if warn_at is not None and absorbed.parts[: len(bd_parts)] != bd_parts:
                build_dir_str = "/".join(bd_parts)
                context = f" (target '{target_name}')" if target_name else ""
                logger.warning(
                    "%s: target path '%s'%s: the leading '%s/' is read as the "
                    "build directory prefix, so the file lands at '%s' inside "
                    "the build directory, not in a '%s' subdirectory of it. "
                    "For the latter, write the prefix twice: '%s/%s'. To say "
                    "the same thing without the ambiguity, drop the prefix: "
                    "'%s'.",
                    warn_at,
                    path_str,
                    context,
                    build_dir_str,
                    absorbed,
                    bd_parts[-1],
                    build_dir_str,
                    path_str,
                    absorbed,
                )
            return absorbed

        return path_obj

    def normalize_source_path(self, path: Path | str) -> Path:
        """Normalize a source (input) path to be relative to project root."""
        path_str = str(path).replace("\\", "/")
        path_obj = Path(path_str)

        if path_obj.is_absolute():
            try:
                return path_obj.relative_to(self.project_root)
            except ValueError:
                # Not under project root - external source
                return path_obj

        return path_obj

    def make_build_relative(self, path: Path) -> Path:
        """Make an absolute path under build_dir relative to it."""
        if path.is_absolute():
            try:
                return path.relative_to(self._resolved_build_dir)
            except ValueError:
                return path
        return path

    def canonicalize(self, path: Path | str) -> Path:
        """Convert to canonical form: project-root-relative or absolute.

        Paths under the project root become relative to it; external absolute
        paths stay absolute; dot segments and backslashes are normalized.
        Pure path arithmetic — no filesystem access.
        """
        path_obj = Path(str(path).replace("\\", "/"))
        if path_obj.is_absolute():
            try:
                return path_obj.relative_to(self.project_root)
            except ValueError:
                return path_obj
        return Path(os.path.normpath(str(path_obj)))

    def make_execution_relative(self, path: Path | str) -> str:
        """Path as seen from the build (execution) directory.

        The path contract for generators that run from the build dir
        (Ninja, Make); see execution_relative() for the rules.
        """
        return execution_relative(
            path,
            execution_dir=self._resolved_build_dir,
            build_dir_parts=self.build_dir.parts
            if not self.build_dir.is_absolute()
            else (),
        )

    def make_project_relative(self, path: Path) -> str:
        """Make a path relative to the project root, as a forward-slash string."""
        if path.is_absolute():
            try:
                return str(path.relative_to(self.project_root)).replace("\\", "/")
            except ValueError:
                # Not under project root
                return str(path).replace("\\", "/")
        return str(path).replace("\\", "/")


def execution_relative(
    path: Path | str,
    *,
    execution_dir: Path | None,
    build_dir_parts: tuple[str, ...],
) -> str:
    """Render *path* relative to the directory a build tool executes in.

    The single home of the generator path contract (ninja and make both run
    from the build directory; see ARCHITECTURE.md "Path handling"):

    - An absolute path under *execution_dir* becomes relative to it.
    - A relative path carrying the *build_dir_parts* prefix (the canonical
      node form, e.g. ``build/obj/foo.o``) has the prefix stripped;
      the build dir itself renders as ``"."``.
    - Anything else (external absolute paths, project-relative sources —
      the caller decides how to anchor those, e.g. ninja's ``$topdir``)
      passes through unchanged.

    Always uses forward slashes, which every supported tool accepts on
    every platform.
    """
    path_obj = Path(path)

    if path_obj.is_absolute():
        if execution_dir is not None:
            try:
                return str(path_obj.relative_to(execution_dir)).replace("\\", "/")
            except ValueError:
                pass
        return str(path_obj).replace("\\", "/")

    if build_dir_parts:
        parts = path_obj.parts
        n = len(build_dir_parts)
        if parts[:n] == build_dir_parts:
            if len(parts) > n:
                return str(Path(*parts[n:])).replace("\\", "/")
            return "."

    return str(path_obj).replace("\\", "/")


def executable_form(path: str, *, windows: bool) -> str:
    """Spell *path* so the shell running the build will execute it.

    The companion of :func:`execution_relative`, which says where a file is;
    this says how to run it from there. A POSIX shell looks a bare name up on
    ``$PATH`` and never in the working directory, so a path without a
    directory needs a ``./``. cmd.exe does search the working directory, but
    reads a leading ``/`` as the start of a switch, so it needs the
    separators the other way round.

    Always the same answer for the same input, so both generators agree.
    """
    if os.path.isabs(path):
        return path.replace("/", "\\") if windows else path
    if windows:
        return path.replace("/", "\\")
    return path if path.startswith((".", "/")) else f"./{path}"
