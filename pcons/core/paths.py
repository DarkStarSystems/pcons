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
        """Initialize the path resolver.

        Args:
            project_root: The root directory of the project.
            build_dir: The build output directory (can be relative or absolute).
        """
        self.project_root = project_root.resolve()
        self.build_dir = build_dir
        # Pre-compute the resolved build_dir for comparison
        if build_dir.is_absolute():
            self._resolved_build_dir = build_dir.resolve()
        else:
            self._resolved_build_dir = (self.project_root / build_dir).resolve()

    def normalize_target_path(
        self, path: Path | str, *, target_name: str | None = None
    ) -> Path:
        """Normalize a target (output) path to be relative to build_dir.

        Handles three cases:
        1. Absolute path under build_dir: Normalize to relative (idempotent)
        2. Relative path starting with build_dir name: WARN but KEEP the path
        3. Normal relative path: Just use it as-is

        Args:
            path: The target path to normalize (Path or str).
            target_name: Optional target name for better warning messages.

        Returns:
            Normalized path relative to build_dir.
        """
        # Accept both Path and str, treat identically
        path_str = str(path)
        # Normalize backslashes to forward slashes
        path_str = path_str.replace("\\", "/")
        path_obj = Path(path_str)

        # Case 1: Absolute path
        if path_obj.is_absolute():
            try:
                # Try to make it relative to build_dir
                return path_obj.relative_to(self._resolved_build_dir)
            except ValueError:
                # Path is not under build_dir - return as-is (external output)
                return path_obj

        # Case 2: Relative path starting with build_dir name
        # This is almost always a mistake: the user passed a project-root-relative
        # path (like "build/foo") but target paths should be build-dir-relative
        # (just "foo"). The build system prepends build_dir, so "build/foo"
        # becomes "build/build/foo".
        build_dir_name = self.build_dir.name
        parts = path_obj.parts
        if parts and parts[0] == build_dir_name:
            suggested = "/".join(parts[1:])
            context = f" (target '{target_name}')" if target_name else ""
            warnings.warn(
                f"Target path '{path}'{context} starts with build directory "
                f"name '{build_dir_name}'. "
                f"This will create '{build_dir_name}/{path}' inside the build "
                f"directory. Target paths are relative to build_dir, so use "
                f"'{suggested}' instead of '{path}'.",
                UserWarning,
                stacklevel=3,  # Skip normalize_target_path and caller
            )
            # Keep the path as-is (don't strip the prefix)
            return path_obj

        # Case 3: Normal relative path - use as-is
        return path_obj

    def normalize_source_path(self, path: Path | str) -> Path:
        """Normalize a source (input) path to be relative to project root.

        Source paths are expected to be relative to the project root.
        Absolute paths within the project are converted to relative.

        Args:
            path: The source path to normalize (Path or str).

        Returns:
            Normalized path (relative to project root if possible).
        """
        # Accept both Path and str, treat identically
        path_str = str(path)
        # Normalize backslashes to forward slashes
        path_str = path_str.replace("\\", "/")
        path_obj = Path(path_str)

        # Absolute path - try to make relative to project root
        if path_obj.is_absolute():
            try:
                return path_obj.relative_to(self.project_root)
            except ValueError:
                # Path is not under project root - return as-is (external source)
                return path_obj

        # Already relative - use as-is
        return path_obj

    def make_build_relative(self, path: Path) -> Path:
        """Make a path relative to the build directory.

        Useful for converting absolute paths to build-relative paths
        for use in ninja files.

        Args:
            path: The path to make relative.

        Returns:
            Path relative to build_dir.
        """
        if path.is_absolute():
            try:
                return path.relative_to(self._resolved_build_dir)
            except ValueError:
                # Not under build_dir - return as-is
                return path
        return path

    def canonicalize(self, path: Path | str) -> Path:
        """Convert to canonical form: project-root-relative or absolute.

        Canonical form means:
        - Paths under project root become relative to project root
        - External absolute paths stay absolute
        - Relative paths are normalized (dot segments removed)
        - Backslashes are normalized to forward slashes

        Uses pure path arithmetic (no filesystem access).

        Args:
            path: The path to canonicalize (Path or str).

        Returns:
            Canonicalized path.
        """
        path_obj = Path(str(path).replace("\\", "/"))
        if path_obj.is_absolute():
            try:
                return path_obj.relative_to(self.project_root)
            except ValueError:
                return path_obj
        return Path(os.path.normpath(str(path_obj)))

    def make_project_relative(self, path: Path) -> str:
        """Make a path relative to the project root.

        Returns a string representation for use in generated files.

        Args:
            path: The path to make relative.

        Returns:
            String representation of the path relative to project root.
        """
        if path.is_absolute():
            try:
                return str(path.relative_to(self.project_root)).replace("\\", "/")
            except ValueError:
                # Not under project root - return as-is
                return str(path).replace("\\", "/")
        return str(path).replace("\\", "/")
