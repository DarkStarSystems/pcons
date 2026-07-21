# SPDX-License-Identifier: MIT
"""Archive tool (command templates) and the Tarfile/Zipfile builders.

Users can customize the archive commands via the tool namespace
(env.archive.tarcmd) or override compression/basedir per target.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING, cast

from pcons.core.builder_registry import builder
from pcons.core.node import BuildInfo, FileNode
from pcons.core.resolver import PendingSourceFactory
from pcons.core.subst import SourcePath, TargetPath
from pcons.core.target import Target
from pcons.tools.tool import StandaloneTool
from pcons.util.source_location import get_caller_location

if TYPE_CHECKING:
    from pcons.core.builder import Builder
    from pcons.core.environment import Environment
    from pcons.core.project import Project


class ArchiveTool(StandaloneTool):
    """Tool for archive creation operations.

    Provides cross-platform archive commands using Python helpers.
    The Tarfile and Zipfile builders reference these command templates.

    Variables:
        tarcmd: Command template for creating tar archives (list of tokens).
                Default: [python, archive_helper.py, --type, tar, ...]
        zipcmd: Command template for creating zip archives (list of tokens).
                Default: [python, archive_helper.py, --type, zip, ...]
        compression: Default compression type for tar (None, "gzip", "bz2", "xz").
        basedir: Default base directory for archive paths.

    Example:
        # Use system tar (as list)
        env.archive.tarcmd = ["tar", "-cvf", "$$TARGET", "-C", "$archive.basedir", "$$SOURCES"]

        # Set default compression
        env.archive.compression = "gzip"
    """

    def __init__(self) -> None:
        super().__init__("archive")

    def default_vars(self) -> dict[str, object]:
        """Return default command templates (cross-platform Python helper)."""
        import pcons.util.archive_helper as archive_mod

        python_cmd = sys.executable.replace("\\", "/")
        helper_path = str(Path(archive_mod.__file__)).replace("\\", "/")

        return {
            "tarcmd": [
                python_cmd,
                helper_path,
                "--type",
                "tar",
                "$archive.compression_flag",
                "--output",
                TargetPath(),
                "--base-dir",
                "$archive.basedir",
                SourcePath(),
            ],
            "zipcmd": [
                python_cmd,
                helper_path,
                "--type",
                "zip",
                "--output",
                TargetPath(),
                "--base-dir",
                "$archive.basedir",
                SourcePath(),
            ],
            "compression": None,
            "basedir": ".",
            # Computed variable — set by ArchiveContext before subst()
            "compression_flag": "",
        }

    def builders(self) -> dict[str, Builder]:
        """Empty: builders are registered via the @builder decorator below."""
        return {}


class ArchiveNodeFactory(PendingSourceFactory):
    """Factory creating archive (tar/zip) output nodes during pending-sources
    resolution."""

    def resolve_pending(self, target: Target) -> None:
        """Resolve pending sources for an archive target (phase 2).

        Runs after main resolution when output_nodes are populated, so
        archive targets can reference outputs from other targets.
        """
        if target._builder_name not in ("Tarfile", "Zipfile"):
            return

        if not target._builder_data:
            return

        resolved_sources = self._resolve_sources(target)
        self._create_archive_node(target, resolved_sources)

    def _create_archive_node(self, target: Target, sources: list[FileNode]) -> None:
        """Create archive output node for a Tarfile or Zipfile target."""
        from pcons.tools.archive_context import ArchiveContext

        build_data = target._builder_data
        if build_data is None:
            return

        output_path = Path(build_data["output"])
        tool = build_data["tool"]

        # Via project.node() for deduplication
        archive_node = self.project.node(output_path)
        archive_node.depends(sources)

        env = getattr(target, "_env", None)
        context = ArchiveContext.from_target(target, env)

        if tool == "tarfile":
            command_var = "tarcmd"
            description = "TAR $out"
        else:  # zipfile
            command_var = "zipcmd"
            description = "ZIP $out"

        archive_node._build_info = cast(
            BuildInfo,
            {
                "tool": "archive",
                "command_var": command_var,
                "sources": sources,
                "description": description,
                # Provides get_env_overrides() for template expansion
                "context": context,
                "env": env,
            },
        )

        target.output_nodes.append(archive_node)


class ArchiveTarget(Target):
    """Target for archive builds with compression and basedir properties.

    The properties override env defaults for this target:

        tarfile = project.Tarfile(env, output="foo.tar.gz", sources=[...])
        tarfile.compression = "xz"
        tarfile.basedir = "src"
    """

    # Overrides live in __dict__: Target uses __slots__, and a subclass
    # can't add new slots.

    @property
    def compression(self) -> str | None:
        """Get the compression type for this archive."""
        override = object.__getattribute__(self, "__dict__").get(
            "_compression_override"
        )
        if override is not None:
            return override
        builder_data = getattr(self, "_builder_data", None) or {}
        return builder_data.get("compression")

    @compression.setter
    def compression(self, value: str | None) -> None:
        """Set the compression type for this archive (overrides env default)."""
        object.__getattribute__(self, "__dict__")["_compression_override"] = value

    @property
    def basedir(self) -> str:
        """Get the base directory for this archive."""
        override = object.__getattribute__(self, "__dict__").get("_basedir_override")
        if override is not None:
            return override
        builder_data = getattr(self, "_builder_data", None) or {}
        return builder_data.get("base_dir", ".")

    @basedir.setter
    def basedir(self, value: str | Path) -> None:
        """Set the base directory for this archive (overrides env default)."""
        object.__getattribute__(self, "__dict__")["_basedir_override"] = str(value)


@builder(
    "Tarfile",
    target_type="archive",
    factory_class=ArchiveNodeFactory,
    requires_env=True,
)
class TarfileBuilder:
    """Create a tar archive from source files/directories.

    Supports all common compression formats: .tar.gz, .tar.bz2, .tar.xz, .tgz, .tar

    The returned ArchiveTarget supports property-based overrides:
        tarfile = project.Tarfile(env, output="foo.tar.gz", sources=[...])
        tarfile.compression = "xz"  # Override for this target
        tarfile.basedir = "src"
    """

    @staticmethod
    def create_target(
        project: Project,
        env: Environment,
        *,
        output: str | Path,
        sources: list[str | Path | FileNode | Target] | None = None,
        compression: str | None = None,
        base_dir: str | Path | None = None,
        name: str | None = None,
    ) -> ArchiveTarget:
        """Create a Tarfile target.

        Args:
            project: The project to add the target to.
            env: Environment for this build.
            output: Output archive path.
            sources: Input files, directories, and/or Targets.
            compression: Compression type (None, "gzip", "bz2", "xz").
            base_dir: Base directory for archive paths.
            name: Optional target name.

        Returns:
            ArchiveTarget representing the archive, with settable properties.
        """
        output_path = project.path_resolver.normalize_target_path(output)

        # Infer compression from extension if not specified
        if compression is None:
            output_str = str(output)
            if output_str.endswith(".tar.gz") or output_str.endswith(".tgz"):
                compression = "gzip"
            elif output_str.endswith(".tar.bz2"):
                compression = "bz2"
            elif output_str.endswith(".tar.xz"):
                compression = "xz"
            # .tar gets no compression

        if name is None:
            name = _name_from_output(
                output, [".tar.gz", ".tar.bz2", ".tar.xz", ".tgz", ".tar"]
            )

        target = ArchiveTarget(
            name,
            target_type="archive",
            defined_at=get_caller_location(),
        )
        target._env = env

        target._builder_name = "Tarfile"
        target._builder_data = {
            "tool": "tarfile",
            "output": str(output_path),
            "compression": compression,
            "base_dir": str(base_dir) if base_dir else ".",
        }
        target._pending_sources = list(sources) if sources else []

        return target


@builder(
    "Zipfile",
    target_type="archive",
    factory_class=ArchiveNodeFactory,
    requires_env=True,
)
class ZipfileBuilder:
    """Create a zip archive from source files/directories.

    The returned ArchiveTarget supports property-based overrides:
        zipfile = project.Zipfile(env, output="foo.zip", sources=[...])
        zipfile.basedir = "src"
    """

    @staticmethod
    def create_target(
        project: Project,
        env: Environment,
        *,
        output: str | Path,
        sources: list[str | Path | FileNode | Target] | None = None,
        base_dir: str | Path | None = None,
        name: str | None = None,
    ) -> ArchiveTarget:
        """Create a Zipfile target.

        Args:
            project: The project to add the target to.
            env: Environment for this build.
            output: Output archive path.
            sources: Input files, directories, and/or Targets.
            base_dir: Base directory for archive paths.
            name: Optional target name.

        Returns:
            ArchiveTarget representing the archive, with settable properties.
        """
        output_path = project.path_resolver.normalize_target_path(output)

        if name is None:
            name = _name_from_output(output, [".zip"])

        target = ArchiveTarget(
            name,
            target_type="archive",
            defined_at=get_caller_location(),
        )
        target._env = env

        target._builder_name = "Zipfile"
        target._builder_data = {
            "tool": "zipfile",
            "output": str(output_path),
            "base_dir": str(base_dir) if base_dir else ".",
        }
        target._pending_sources = list(sources) if sources else []

        return target


def _name_from_output(output: str | Path, suffixes: list[str]) -> str:
    """Derive target name from output path by stripping archive suffixes.

    Args:
        output: Output path (e.g., "dist/docs.tar.gz").
        suffixes: List of suffixes to strip.

    Returns:
        Derived name (e.g., "dist/docs").
    """
    name = str(output)
    for suffix in suffixes:
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    return name
