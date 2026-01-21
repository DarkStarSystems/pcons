# SPDX-License-Identifier: MIT
"""Archive builders for creating tar and zip files.

This module provides builders for archive creation:
- Tarfile: Create tar archives (.tar, .tar.gz, .tar.bz2, .tar.xz)
- Zipfile: Create zip archives (.zip)
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from pcons.core.builder_registry import builder
from pcons.core.node import FileNode
from pcons.core.target import Target, TargetType
from pcons.util.source_location import get_caller_location

if TYPE_CHECKING:
    from pcons.core.environment import Environment
    from pcons.core.project import Project


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

        # Create the archive output node
        archive_node = FileNode(output_path, defined_at=get_caller_location())
        archive_node.depends(sources)

        # Build the build_info for the generator
        archive_node._build_info = {
            "tool": build_data["tool"],
            "output": str(output_path),
            "sources": sources,
            "base_dir": build_data.get("base_dir", "."),
        }

        # Add compression for tarfiles
        if "compression" in build_data:
            archive_node._build_info["compression"] = build_data["compression"]

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
