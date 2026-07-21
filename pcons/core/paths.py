# SPDX-License-Identifier: MIT
"""Path resolution utilities for consistent output path handling.

PathResolver provides centralized path handling where:
- Target (output) paths are relative to build_dir
- Source (input) paths are relative to project root
- Absolute paths pass through unchanged
- Path and string arguments behave identically
"""

from __future__ import annotations

import os
import warnings
from pathlib import Path


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
        self, path: Path | str, *, target_name: str | None = None
    ) -> Path:
        """Normalize a target (output) path to be relative to build_dir.

        A relative path that starts with the build_dir prefix warns but is
        kept as-is.
        """
        path_str = str(path).replace("\\", "/")
        path_obj = Path(path_str)

        if path_obj.is_absolute():
            try:
                return path_obj.relative_to(self._resolved_build_dir)
            except ValueError:
                # Not under build_dir - external output
                return path_obj

        # A build_dir prefix on a relative path is almost always a mistake:
        # target paths are build-dir-relative, so "build/foo" would become
        # "build/build/foo".
        bd_parts = self.build_dir.parts
        parts = path_obj.parts
        if bd_parts and parts[: len(bd_parts)] == bd_parts:
            suggested = "/".join(parts[len(bd_parts) :])
            build_dir_str = str(self.build_dir)
            context = f" (target '{target_name}')" if target_name else ""
            warnings.warn(
                f"Target path '{path}'{context} starts with build directory "
                f"'{build_dir_str}'. "
                f"This will create '{build_dir_str}/{path}' inside the build "
                f"directory. Target paths are relative to build_dir, so use "
                f"'{suggested}' instead of '{path}'.",
                UserWarning,
                stacklevel=3,  # Skip normalize_target_path and caller
            )
            return path_obj

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
