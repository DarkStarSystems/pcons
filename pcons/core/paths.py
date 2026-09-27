# SPDX-License-Identifier: MIT
"""Path resolution utilities for consistent output path handling.

PathResolver provides centralized path handling where:
- Target (output) paths are relative to build_dir
- Source (input) paths are relative to project or subproject root
- Absolute paths pass through unchanged
- Path and string arguments behave identically
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Location:
    """Where a node path points, in the terms a build script uses.

    A target's file is named relative to the build directory, a source's
    relative to the project's top, and a file outside both absolutely. That
    is also what a user types: ``pcons build obj/app.o``, or ``src/main.c``
    in ``sources=``.

    Attributes:
        anchor: ``"build"`` for a file in the build directory, ``"top"`` for
            one elsewhere in the project tree, ``"outside"`` for anything else.
        path: The path from that anchor: ``.`` for the anchor itself, and
            absolute when the anchor is ``"outside"``.
    """

    anchor: Literal["build", "top", "outside"]
    path: Path

    def __str__(self) -> str:
        return self.path.as_posix()


class PathResolver:
    """Centralized path handling for pcons builds.

    A resolver is a pair of anchors: a project root that source (input)
    paths are resolved against, and a build directory that target (output)
    paths are resolved against. Absolute paths pass through. It does path
    arithmetic only and never touches the filesystem.

    Node paths are canonical in the top-level project's frame: relative to
    the top root, with the build directory as a prefix when it lies inside
    the tree. ``Project.top_path_resolver`` is anchored there and is the one
    to use for a path that is already canonical, such as a node's, or to
    make one execution-relative for a command line.

    ``add_subdirectory`` complicates the picture in two ways, and the
    resolver stays simple by taking both as arguments:

    * A subdirectory script's relative paths are read against its own
      directory. ``subdir()`` returns a resolver moved down by that offset,
      root and build directory alike, which is what ``Project.path_resolver``
      uses while such a script runs and during its resolve pass.

    * A target's outputs land under the environment's build directory, which
      is the top build directory, then the environment's ``build_prefix`` if
      it has one, then the declaring script's offset. ``Environment.build_dir_for``
      composes that, and callers hand it to ``normalize_target_path`` as
      ``build_dir=``. Absorption of a written-out prefix accepts either that
      base or the resolver's own, so ``project.build_dir / "x.h"`` and
      ``"x.h"`` keep meaning the same file whichever way they were written.

    Attributes:
        project_root: The root directory of the project, absolute.
        build_dir: The build output directory, relative to the project root
            or absolute. When this is absolute (outside the tree), no build-directory
            prefix appears in node paths.
    """

    __slots__ = ("project_root", "build_dir", "_resolved_build_dir")

    def __init__(self, project_root: Path, build_dir: Path) -> None:
        self.project_root = project_root.resolve()
        self.build_dir = build_dir
        if build_dir.is_absolute():
            self._resolved_build_dir = build_dir.resolve()
        else:
            self._resolved_build_dir = (self.project_root / build_dir).resolve()

    @property
    def execution_dir(self) -> Path:
        """The absolute directory build commands run in: the build directory."""
        return self._resolved_build_dir

    def anchor_script_path(self, path: Path | str, offset: Path) -> Path:
        """A path a build script wrote, as a node path.

        *offset* is the script's directory, from the top-level root. A
        relative path is read from there, as ``sources=`` reads it, unless it
        starts with the build directory: ``project.build_dir / "gen/x.c"`` is
        already a path in the build tree. Absolute paths pass through.
        """
        p = Path(path)
        if p.is_absolute() or not offset.parts:
            return p
        return offset / p

    def subdir(self, subdir: str | Path) -> PathResolver:
        """Return a new PathResolver with project_root and build_dir in *subdir*."""
        return PathResolver(self.project_root / subdir, self.build_dir / subdir)

    def normalize_target_path(
        self, path: Path | str, *, build_dir: Path | None = None
    ) -> Path:
        """A target (output) path, relative to the build directory.

        A relative path is taken as written, so ``"build/x.h"`` is a
        ``build`` subdirectory of the build directory. An absolute path under
        the build directory comes back relative to it; one outside is an
        external output and stays absolute.

        *build_dir* overrides the base directory (relative to the project
        root, or absolute), for callers that anchor somewhere other than this
        resolver's own build_dir: a sub-project's directory, or an
        environment with a ``build_prefix``.
        """
        if build_dir is None:
            resolved_bd = self._resolved_build_dir
        elif build_dir.is_absolute():
            resolved_bd = build_dir.resolve()
        else:
            resolved_bd = (self.project_root / build_dir).resolve()

        path_obj = Path(str(path).replace("\\", "/"))
        if path_obj.is_absolute():
            try:
                return path_obj.resolve().relative_to(resolved_bd)
            except ValueError:
                return path_obj  # Not under build_dir: an external output
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

    def locate(self, path: Path | str, *, built: bool) -> Location:
        """Where *path* points: in the build directory, elsewhere under the
        project top, or outside both.

        Args:
            path: A node path. Node paths are either relative to the
                project top or absolute. With built=True, a relative path without the
                build directory's prefix is interpreted as relative to the
                build directory instead of top.
            built: The build writes this file, so a relative path that
                doesn't start with the build directory is interpreted as
                relative to the build directory, rather than a
                source's, relative to the top.
        """
        p = Path(str(path).replace("\\", "/"))
        # Rooted, even without a drive (Windows' "\\opt\\app"): not relative
        # to anything of the project's.
        if p.anchor:
            # The anchors are resolved; the path may be written through a
            # symlink (macOS's /var for /private/var, say).
            for candidate in (p, p.resolve()):
                for anchor, base in (
                    ("build", self._resolved_build_dir),
                    ("top", self.project_root),
                ):
                    if candidate.is_relative_to(base):
                        return Location(anchor, candidate.relative_to(base))
            return Location("outside", p)
        prefix = () if self.build_dir.is_absolute() else self.build_dir.parts
        if prefix and p.parts[: len(prefix)] == prefix:
            return Location("build", Path(*p.parts[len(prefix) :]))
        if built:
            return Location("build", p)
        normal = Path(os.path.normpath(p))
        if normal.parts[:1] == ("..",):
            return Location("outside", self.project_root / normal)
        return Location("top", normal)

    def path_text(
        self,
        path: Path | str,
        *,
        built: bool,
        run_dir: Path | None = None,
        top: str | None = None,
    ) -> str:
        """*path* as a program running in *run_dir* will see it.

        Relative when possible, rooted at *run_dir* or self's build dir.
        run_dir can be different when using `cwd=`, for instance.
        When provided, *top* is how the caller wants the top of a relative path
        represented, e.g. ``$topdir`` for ninja.
        A file outside the project stays
        absolute, written as the host platform writes it; everything else has
        forward slashes.

        Args:
            path: A node path; see :meth:`locate`.
            built: The build writes this file; see :meth:`locate`.
            run_dir: The absolute directory the reader runs in, when it isn't
                the build directory.
            top: How the reader writes the project top, for a source's path
                seen from the build directory: ``"$topdir"`` for ninja, the
                absolute top for make. Ignored with *run_dir*.
        """
        where = self.locate(path, built=built)
        if where.anchor == "outside":
            return str(where.path)  # As the platform writes it, as it was given.
        if run_dir is None and where.anchor == "build":
            return str(where)
        if run_dir is None and top is not None:
            return top if where.path == Path(".") else f"{top}/{where}"
        base = (
            self._resolved_build_dir if where.anchor == "build" else self.project_root
        )
        absolute = base / where.path
        try:
            text = os.path.relpath(absolute, run_dir or self._resolved_build_dir)
        except ValueError:  # Windows: different drives
            return absolute.as_posix()
        return text.replace(os.sep, "/")


def executable_form(path: str, *, windows: bool) -> str:
    """Return a form of *path* so the shell running the build will execute it.

    The companion of :meth:`PathResolver.path_text`, which says where a file
    is; this says how to run it from there. A POSIX shell looks a bare name up on
    ``$PATH`` and never in the working directory, so a path without a
    directory needs a ``./``. cmd.exe does search the working directory, but
    reads a leading ``/`` as the start of a switch, so it needs the
    separators the other way round.
    """
    if os.path.isabs(path):
        return path.replace("/", "\\") if windows else path
    if windows:
        return path.replace("/", "\\")
    return path if path.startswith((".", "/")) else f"./{path}"
