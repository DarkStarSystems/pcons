# SPDX-License-Identifier: MIT
"""Archive tool and builders for creating tar and zip archives.

This module provides:
- ArchiveTool: Standalone tool with command templates (tarcmd, zipcmd)
- Tarfile: Builder for tar archives (.tar, .tar.gz, .tar.bz2, .tar.xz)
- Zipfile: Builder for zip archives (.zip)

Users can customize the archive commands via the tool namespace:
    env.archive.tarcmd = "tar -cvf $out -C $basedir $in"  # Use system tar
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

from pcons.core.builder_registry import builder
from pcons.core.node import FileNode
from pcons.core.target import Target, TargetType
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
        tarcmd: Command template for creating tar archives.
                Default: python archive_helper.py --type tar ...
        zipcmd: Command template for creating zip archives.
                Default: python archive_helper.py --type zip ...

    Example:
        # Use system tar
        env.archive.tarcmd = "tar -cvf $out -C $basedir $in"

        # Use custom compression
        env.archive.tarcmd = "tar -czvf $out -C $basedir $in"
    """

    def __init__(self) -> None:
        """Initialize the archive tool."""
        super().__init__("archive")

    def default_vars(self) -> dict[str, object]:
        """Return default command templates.

        Uses Python helper script for cross-platform compatibility.
        The $$ escaping preserves $ for ninja variable substitution.
        """
        import pcons.util.archive_helper as archive_mod

        # Use escaped $$ for ninja variables since these
        # get processed by env.subst() before going to ninja
        python_cmd = sys.executable.replace("\\", "/")
        helper_path = str(Path(archive_mod.__file__)).replace("\\", "/")

        return {
            # Tar command: $compression_flag and $basedir are per-build variables
            "tarcmd": (
                f"{python_cmd} {helper_path} --type tar "
                "$$compression_flag --output $$out --base-dir $$basedir $$in"
            ),
            # Zip command: $basedir is a per-build variable
            "zipcmd": (
                f"{python_cmd} {helper_path} --type zip "
                "--output $$out --base-dir $$basedir $$in"
            ),
        }

    def builders(self) -> dict[str, Builder]:
        """Return builders provided by this tool.

        Returns empty dict - builders are registered via @builder decorator
        below and accessed via project.Tarfile() / project.Zipfile().
        """
        return {}


class ArchiveNodeFactory:
    """Factory for creating archive (tar/zip) output nodes.

    Handles creation of nodes for Tarfile and Zipfile targets.
    This factory is used during the pending-sources resolution phase.
    """

    def __init__(self, project: Project) -> None:
        """Initialize the factory.

        Args:
            project: The project to resolve.
        """
        self.project = project

    def resolve(self, target: Target, env: Environment | None) -> None:
        """Resolve the target (phase 1).

        Archive targets don't need phase 1 resolution - they only handle
        pending sources in phase 2.
        """
        pass

    def resolve_pending(self, target: Target) -> None:
        """Resolve pending sources for an archive target (phase 2).

        This is called after main resolution when output_nodes are populated,
        allowing archive targets to reference outputs from other targets.
        """
        if target._builder_name not in ("Tarfile", "Zipfile"):
            return

        if target._builder_data is None:
            return

        # Resolve pending sources to FileNodes
        resolved_sources = self._resolve_sources(target)

        # Create the archive node
        self._create_archive_node(target, resolved_sources)

    def _resolve_sources(self, target: Target) -> list[FileNode]:
        """Resolve pending sources to FileNodes."""
        from pcons.core.node import Node

        if target._pending_sources is None:
            return []

        resolved: list[FileNode] = []
        for source in target._pending_sources:
            if isinstance(source, Target):
                # Get output files from the resolved target
                resolved.extend(source.output_nodes)
                # Also check nodes directly (for interface targets)
                for node in source.nodes:
                    if isinstance(node, FileNode) and node not in resolved:
                        resolved.append(node)
            elif isinstance(source, FileNode):
                resolved.append(source)
            elif isinstance(source, Node):
                # Skip non-file nodes
                pass
            elif isinstance(source, (Path, str)):
                resolved.append(self.project.node(source))

        return resolved

    def _create_archive_node(self, target: Target, sources: list[FileNode]) -> None:
        """Create archive output node for a Tarfile or Zipfile target."""
        build_data = target._builder_data
        if build_data is None:
            return

        output_path = Path(build_data["output"])
        tool = build_data["tool"]
        base_dir = build_data.get("base_dir", ".")

        # Create the archive output node
        archive_node = FileNode(output_path, defined_at=get_caller_location())
        archive_node.depends(sources)

        # Build per-build variables for the command template
        variables: dict[str, str] = {"basedir": str(base_dir)}

        if tool == "tarfile":
            compression = build_data.get("compression")
            # compression_flag is a per-build variable
            if compression:
                variables["compression_flag"] = f"--compression {compression}"
            else:
                variables["compression_flag"] = ""
            command_var = "tarcmd"
            description = "TAR $out"
        else:  # zipfile
            command_var = "zipcmd"
            description = "ZIP $out"

        # Store build info referencing env.archive.tarcmd or env.archive.zipcmd
        archive_node._build_info = {
            "tool": "archive",
            "command_var": command_var,
            "sources": sources,
            "description": description,
            # Per-build variables for this specific target
            "variables": variables,
        }

        # Add to target's output nodes
        target.output_nodes.append(archive_node)
        target.nodes.append(archive_node)

        # Register with project
        if output_path not in self.project._nodes:
            self.project._nodes[output_path] = archive_node


@builder(
    "Tarfile",
    target_type=TargetType.ARCHIVE,
    factory_class=ArchiveNodeFactory,
    requires_env=True,
)
class TarfileBuilder:
    """Create a tar archive from source files/directories.

    Supports all common compression formats: .tar.gz, .tar.bz2, .tar.xz, .tgz, .tar
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
    ) -> Target:
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
            Target representing the archive.
        """
        # Normalize output path using PathResolver
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

        # Derive name from output if not specified
        if name is None:
            name = _name_from_output(
                output, [".tar.gz", ".tar.bz2", ".tar.xz", ".tgz", ".tar"]
            )

        target = Target(
            name,
            target_type=TargetType.ARCHIVE,
            defined_at=get_caller_location(),
        )
        target._env = env
        target._project = project

        # Set builder metadata
        target._builder_name = "Tarfile"
        target._builder_data = {
            "tool": "tarfile",
            "output": str(output_path),
            "compression": compression,
            "base_dir": str(base_dir) if base_dir else ".",
        }
        target._pending_sources = list(sources) if sources else []

        project.add_target(target)
        return target


@builder(
    "Zipfile",
    target_type=TargetType.ARCHIVE,
    factory_class=ArchiveNodeFactory,
    requires_env=True,
)
class ZipfileBuilder:
    """Create a zip archive from source files/directories."""

    @staticmethod
    def create_target(
        project: Project,
        env: Environment,
        *,
        output: str | Path,
        sources: list[str | Path | FileNode | Target] | None = None,
        base_dir: str | Path | None = None,
        name: str | None = None,
    ) -> Target:
        """Create a Zipfile target.

        Args:
            project: The project to add the target to.
            env: Environment for this build.
            output: Output archive path.
            sources: Input files, directories, and/or Targets.
            base_dir: Base directory for archive paths.
            name: Optional target name.

        Returns:
            Target representing the archive.
        """
        # Normalize output path using PathResolver
        output_path = project.path_resolver.normalize_target_path(output)

        # Derive name from output if not specified
        if name is None:
            name = _name_from_output(output, [".zip"])

        target = Target(
            name,
            target_type=TargetType.ARCHIVE,
            defined_at=get_caller_location(),
        )
        target._env = env
        target._project = project

        # Set builder metadata
        target._builder_name = "Zipfile"
        target._builder_data = {
            "tool": "zipfile",
            "output": str(output_path),
            "base_dir": str(base_dir) if base_dir else ".",
        }
        target._pending_sources = list(sources) if sources else []

        project.add_target(target)
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
