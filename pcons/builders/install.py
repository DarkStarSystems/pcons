# SPDX-License-Identifier: MIT
"""Install builders for copying files to destinations.

This module provides builders for file installation:
- Install: Copy multiple files to a destination directory
- InstallAs: Copy a single file to a specific destination path (with rename)
- InstallDir: Recursively copy a directory tree
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from pcons.core.builder_registry import builder
from pcons.core.node import FileNode
from pcons.core.target import Target, TargetType
from pcons.util.source_location import get_caller_location

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from pcons.core.environment import Environment
    from pcons.core.project import Project


class InstallNodeFactory:
    """Factory for creating install/copy nodes.

    Handles creation of nodes for Install, InstallAs, and InstallDir targets.
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

        Install targets don't need phase 1 resolution - they only handle
        pending sources in phase 2.
        """
        pass

    def resolve_pending(self, target: Target) -> None:
        """Resolve pending sources for an install target (phase 2).

        This is called after main resolution when output_nodes are populated,
        allowing Install targets to reference outputs from other targets.
        """
        if target._builder_data is None:
            return

        builder_name = target._builder_name
        if builder_name not in ("Install", "InstallAs", "InstallDir"):
            return

        # Resolve pending sources to FileNodes
        resolved_sources = self._resolve_sources(target)

        if builder_name == "Install":
            dest_dir = Path(target._builder_data["dest_dir"])
            self._create_install_nodes(target, resolved_sources, dest_dir)
        elif builder_name == "InstallAs":
            dest = Path(target._builder_data["dest"])
            self._create_install_as_node(target, resolved_sources, dest)
        elif builder_name == "InstallDir":
            dest_dir = Path(target._builder_data["dest_dir"])
            self._create_install_dir_node(target, resolved_sources, dest_dir)

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

    def _create_install_nodes(
        self, target: Target, sources: list[FileNode], dest_dir: Path
    ) -> None:
        """Create copy nodes for Install target."""
        # Normalize destination directory using PathResolver
        path_resolver = self.project.path_resolver
        dest_dir = path_resolver.normalize_target_path(dest_dir)

        # Use pcons helper for cross-platform copy
        python_cmd = sys.executable.replace("\\", "/")
        copy_cmd = f"{python_cmd} -m pcons.util.commands copy"

        installed_nodes: list[FileNode] = []
        for file_node in sources:
            if not isinstance(file_node, FileNode):
                continue

            # Destination path
            dest_path = dest_dir / file_node.path.name

            # Create destination node
            dest_node = FileNode(dest_path, defined_at=get_caller_location())
            dest_node.depends([file_node])

            # Store build info for the copy command
            dest_node._build_info = {
                "tool": "copy",
                "command": copy_cmd,
                "command_var": "copycmd",
                "sources": [file_node],
                "copy_cmd": f"{copy_cmd} $in $out",
            }

            installed_nodes.append(dest_node)

            # Register the node with the project
            if dest_path not in self.project._nodes:
                self.project._nodes[dest_path] = dest_node

        # Add installed files as output nodes
        target._install_nodes = installed_nodes
        target.output_nodes.extend(installed_nodes)

    def _create_install_as_node(
        self, target: Target, sources: list[FileNode], dest: Path
    ) -> None:
        """Create copy node for InstallAs target."""
        if not sources:
            return

        if len(sources) > 1:
            from pcons.core.errors import BuilderError

            raise BuilderError(
                f"InstallAs expects exactly one source, got {len(sources)}. "
                f"Use Install() for multiple files.",
                location=target.defined_at,
            )

        # Normalize destination path using PathResolver
        path_resolver = self.project.path_resolver
        dest = path_resolver.normalize_target_path(dest)

        # Use pcons helper for cross-platform copy
        python_cmd = sys.executable.replace("\\", "/")
        copy_cmd = f"{python_cmd} -m pcons.util.commands copy"

        source_node = sources[0]

        # Create destination node
        dest_node = FileNode(dest, defined_at=get_caller_location())
        dest_node.depends([source_node])

        dest_node._build_info = {
            "tool": "copy",
            "command": copy_cmd,
            "command_var": "copycmd",
            "sources": [source_node],
            "copy_cmd": f"{copy_cmd} $in $out",
        }

        # Add installed file as output node
        target._install_nodes = [dest_node]
        target.output_nodes.append(dest_node)

        if dest not in self.project._nodes:
            self.project._nodes[dest] = dest_node

    def _create_install_dir_node(
        self, target: Target, sources: list[FileNode], dest_dir: Path
    ) -> None:
        """Create copytree node for InstallDir target."""
        if not sources:
            return

        if len(sources) > 1:
            from pcons.core.errors import BuilderError

            raise BuilderError(
                f"InstallDir expects exactly one source directory, got {len(sources)}.",
                location=target.defined_at,
            )

        # Normalize destination directory using PathResolver
        path_resolver = self.project.path_resolver
        dest_dir = path_resolver.normalize_target_path(dest_dir)

        # Use pcons helper for cross-platform copytree
        python_cmd = sys.executable.replace("\\", "/")
        copytree_cmd = f"{python_cmd} -m pcons.util.commands copytree"

        source_node = sources[0]
        source_path = source_node.path

        # Destination is dest_dir / source directory name
        dest_path = dest_dir / source_path.name

        # Put stamp files in a dedicated .stamps directory
        stamps_dir = self.project.build_dir / ".stamps"
        stamp_name = str(dest_path).replace("/", "_").replace("\\", "_") + ".stamp"
        stamp_path = stamps_dir / stamp_name

        # Create stamp node (this is what ninja tracks)
        stamp_node = FileNode(stamp_path, defined_at=get_caller_location())
        stamp_node.depends([source_node])

        # Build the command with paths relative to build directory
        try:
            rel_dest = dest_path.relative_to(self.project.build_dir)
        except ValueError:
            rel_dest = dest_path

        stamp_node._build_info = {
            "tool": "copytree",
            "command": copytree_cmd,
            "command_var": "copytreecmd",
            "sources": [source_node],
            "copytree_cmd": f"{copytree_cmd} --depfile $out.d --stamp $out $in {rel_dest}",
            "depfile": "$out.d",
            "deps_style": "gcc",
        }

        # Add stamp node as output
        target._install_nodes = [stamp_node]
        target.output_nodes.append(stamp_node)

        if stamp_path not in self.project._nodes:
            self.project._nodes[stamp_path] = stamp_node


@builder("Install", target_type=TargetType.INTERFACE, factory_class=InstallNodeFactory)
class InstallBuilder:
    """Install files to a destination directory.

    Creates copy operations for each source file to the destination
    directory. The returned target depends on all the installed files.
    """

    @staticmethod
    def create_target(
        project: Project,
        dest_dir: Path | str,
        sources: list[Target | FileNode | Path | str],
        *,
        name: str | None = None,
    ) -> Target:
        """Create an Install target.

        Args:
            project: The project to add the target to.
            dest_dir: Destination directory path.
            sources: Files to install.
            name: Optional name for the install target.

        Returns:
            A Target representing the install operation.
        """
        dest_dir = Path(dest_dir)
        target_name = name or f"install_{dest_dir.name}"

        # Handle duplicate target names
        base_name = target_name
        counter = 1
        while project.get_target(target_name) is not None:
            target_name = f"{base_name}_{counter}"
            counter += 1
        if target_name != base_name:
            logger.warning(
                "Install target renamed from '%s' to '%s' to avoid conflict",
                base_name,
                target_name,
            )

        # Create the install target
        install_target = Target(
            target_name,
            target_type=TargetType.INTERFACE,
            defined_at=get_caller_location(),
        )

        # Set builder metadata for factory dispatch
        install_target._builder_name = "Install"
        install_target._builder_data = {"dest_dir": str(dest_dir)}
        install_target._pending_sources = list(sources)

        project.add_target(install_target)
        return install_target


@builder(
    "InstallAs", target_type=TargetType.INTERFACE, factory_class=InstallNodeFactory
)
class InstallAsBuilder:
    """Install a file to a specific destination path.

    Unlike Install(), this copies a single file to an exact path,
    allowing rename during installation.
    """

    @staticmethod
    def create_target(
        project: Project,
        dest: Path | str,
        source: Target | FileNode | Path | str,
        *,
        name: str | None = None,
    ) -> Target:
        """Create an InstallAs target.

        Args:
            project: The project to add the target to.
            dest: Full destination path (including filename).
            source: Source file.
            name: Optional name for the install target.

        Returns:
            A Target representing the install operation.
        """
        dest = Path(dest)
        target_name = name or f"install_{dest.name}"

        # Handle duplicate target names
        base_name = target_name
        counter = 1
        while project.get_target(target_name) is not None:
            target_name = f"{base_name}_{counter}"
            counter += 1
        if target_name != base_name:
            logger.warning(
                "Install target renamed from '%s' to '%s' to avoid conflict",
                base_name,
                target_name,
            )

        # Create the install target
        install_target = Target(
            target_name,
            target_type=TargetType.INTERFACE,
            defined_at=get_caller_location(),
        )

        # Set builder metadata for factory dispatch
        install_target._builder_name = "InstallAs"
        install_target._builder_data = {"dest": str(dest)}
        install_target._pending_sources = [source]

        project.add_target(install_target)
        return install_target


@builder(
    "InstallDir", target_type=TargetType.INTERFACE, factory_class=InstallNodeFactory
)
class InstallDirBuilder:
    """Install a directory tree to a destination.

    Recursively copies an entire directory tree. Uses ninja's depfile
    mechanism for incremental rebuilds.
    """

    @staticmethod
    def create_target(
        project: Project,
        dest_dir: Path | str,
        source: Target | FileNode | Path | str,
        *,
        name: str | None = None,
    ) -> Target:
        """Create an InstallDir target.

        Args:
            project: The project to add the target to.
            dest_dir: Destination directory.
            source: Source directory.
            name: Optional name for the install target.

        Returns:
            A Target representing the install operation.
        """
        dest_dir = Path(dest_dir)
        target_name = name or f"install_dir_{dest_dir.name}"

        # Handle duplicate target names
        base_name = target_name
        counter = 1
        while project.get_target(target_name) is not None:
            target_name = f"{base_name}_{counter}"
            counter += 1
        if target_name != base_name:
            logger.warning(
                "InstallDir target renamed from '%s' to '%s' to avoid conflict",
                base_name,
                target_name,
            )

        # Create the install target
        install_target = Target(
            target_name,
            target_type=TargetType.INTERFACE,
            defined_at=get_caller_location(),
        )

        # Set builder metadata for factory dispatch
        install_target._builder_name = "InstallDir"
        install_target._builder_data = {"dest_dir": str(dest_dir)}
        install_target._pending_sources = [source]

        project.add_target(install_target)
        return install_target
