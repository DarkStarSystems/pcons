# SPDX-License-Identifier: MIT
"""Install tool and builders for copying files to destinations.

This module provides:
- InstallTool: Standalone tool with command templates (copycmd, copytreecmd)
- Install: Builder for copying multiple files to a destination directory
- InstallAs: Builder for copying a single file to a specific path (with rename)
- InstallDir: Builder for recursively copying a directory tree

Users can customize the copy commands via the tool namespace:
    env.install.copycmd = ["cp", SourcePath(), TargetPath()]  # Use system cp

Target-level overrides are supported for InstallDir:
    install_dir = project.InstallDir("dist/", source_dir)
    install_dir.destdir = "custom_dest"  # Override for this target
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING, cast

from pcons.core.builder_registry import builder
from pcons.core.node import BuildInfo, FileNode
from pcons.core.subst import PathToken, SourcePath, TargetPath
from pcons.core.target import Target, TargetType
from pcons.tools.tool import StandaloneTool
from pcons.util.source_location import get_caller_location

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from pcons.core.builder import Builder
    from pcons.core.environment import Environment
    from pcons.core.project import Project


class InstallTool(StandaloneTool):
    """Tool for file and directory installation operations.

    Provides cross-platform copy commands using Python helpers.
    The Install, InstallAs, and InstallDir builders reference these
    command templates.

    Variables:
        copycmd: Command template for single file copy (list of tokens).
                 Default: [python, -m, pcons.util.commands, copy, $$SOURCE, $$TARGET]
        copytreecmd: Command template for directory tree copy (list of tokens).
                     Default: [python, -m, pcons.util.commands, copytree, ...]
        destdir: Default destination directory for InstallDir.

    Example:
        # Use system copy on Unix (as list)
        env.install.copycmd = ["cp", "$$SOURCE", "$$TARGET"]

        # Use rsync for directory copies
        env.install.copytreecmd = ["rsync", "-a", "$$SOURCE", "$destdir"]
    """

    def __init__(self) -> None:
        """Initialize the install tool."""
        super().__init__("install")

    def default_vars(self) -> dict[str, object]:
        """Return default command templates.

        Uses Python helper scripts for cross-platform compatibility.
        Commands are lists of tokens for proper handling of paths with spaces.
        SourcePath/TargetPath markers are converted by generators to appropriate
        syntax ($in/$out for Ninja, actual paths for Makefile).
        """
        python_cmd = sys.executable.replace("\\", "/")
        return {
            # Simple file copy: copy source to target
            "copycmd": [
                python_cmd,
                "-m",
                "pcons.util.commands",
                "copy",
                SourcePath(),
                TargetPath(),
            ],
            # Directory tree copy with depfile support
            # $install.destdir is expanded by pcons subst() at generation time
            "copytreecmd": [
                python_cmd,
                "-m",
                "pcons.util.commands",
                "copytree",
                "--depfile",
                TargetPath(suffix=".d"),
                "--stamp",
                TargetPath(),
                SourcePath(),
                "$install.destdir",
            ],
            # Default destination directory (can be overridden per-target)
            "destdir": "",
        }

    def builders(self) -> dict[str, Builder]:
        """Return builders provided by this tool.

        Returns empty dict - builders are registered via @builder decorator
        below and accessed via project.Install() / project.InstallAs() /
        project.InstallDir().
        """
        return {}


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
        if not target._builder_data:
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

    def _get_install_env(self, target: Target) -> Environment | None:
        """Get an environment that has the install tool.

        First tries to get env from target, then falls back to finding
        an environment from the project that has the install tool set up.
        """
        # Try target's env first
        env = getattr(target, "_env", None)
        if env is not None:
            return env

        # Fall back to project environments
        for e in self.project.environments:
            if hasattr(e, "install"):
                return e

        return None

    def _has_child_nodes(self, path: Path) -> bool:
        """Check if any project nodes are children of the given path.

        After resolve, the node graph is populated with all output nodes.
        If any node's path is a descendant of the given path, then the
        path represents a directory.  This avoids filesystem checks.

        Both the input path and node paths are normalized to build-dir-relative
        form before comparison, since sources passed as
        ``project.build_dir / subdir / ...`` include the build_dir prefix
        while node paths in ``project._nodes`` may be build-dir-relative.
        """
        canonicalize = self.project.path_resolver.canonicalize
        build_dir_name = self.project.build_dir.name

        def to_build_relative(p: Path) -> Path:
            """Strip build_dir prefix to get build-dir-relative path."""
            parts = p.parts
            if parts and parts[0] == build_dir_name:
                return Path(*parts[1:]) if len(parts) > 1 else Path(".")
            return p

        check_path = to_build_relative(canonicalize(path))
        for node_path in self.project._nodes:
            canonical = to_build_relative(canonicalize(node_path))
            if canonical != check_path:
                try:
                    canonical.relative_to(check_path)
                    return True
                except ValueError:
                    continue
        return False

    def _create_install_nodes(
        self, target: Target, sources: list[FileNode], dest_dir: Path
    ) -> None:
        """Create copy nodes for Install target.

        For each source, checks whether the source represents a directory
        (by examining the project node graph for child nodes).  Directory
        sources are handled with copytreecmd (depfile + stamp), while file
        sources use copycmd.
        """
        # Normalize destination directory using PathResolver
        path_resolver = self.project.path_resolver
        dest_dir = path_resolver.normalize_target_path(
            dest_dir, target_name=target.name
        )

        # Get environment with install tool
        env = self._get_install_env(target)

        installed_nodes: list[FileNode] = []
        for file_node in sources:
            if not isinstance(file_node, FileNode):
                continue

            # Check if this source is a directory by examining the node graph
            if self._has_child_nodes(file_node.path):
                self._create_install_dir_node_for(
                    target, file_node, dest_dir, env, installed_nodes
                )
                continue

            # Destination path
            dest_path = dest_dir / file_node.path.name

            # Create destination node
            dest_node = FileNode(dest_path, defined_at=get_caller_location())
            dest_node.depends([file_node])

            # Store build info referencing env.install.copycmd
            # The command template comes from the install tool's default_vars
            dest_node._build_info = {
                "tool": "install",
                "command_var": "copycmd",
                "sources": [file_node],
                "description": "INSTALL $out",
                "env": env,
            }

            installed_nodes.append(dest_node)

            # Register the node with the project (canonicalize for consistency)
            canonical = self.project.path_resolver.canonicalize(dest_path)
            if canonical not in self.project._nodes:
                self.project._nodes[canonical] = dest_node

        # Add installed files as output nodes
        target._install_nodes = installed_nodes
        target.output_nodes.extend(installed_nodes)

    def _create_install_dir_node_for(
        self,
        target: Target,
        source_node: FileNode,
        dest_dir: Path,
        env: Environment | None,
        installed_nodes: list[FileNode],
    ) -> None:
        """Create a copytree node for a directory source within Install.

        This is used when _create_install_nodes detects that a source is
        a directory (has child nodes in the project graph).  Uses the same
        copytreecmd + depfile/stamp mechanism as InstallDir.
        """
        from pcons.tools.archive_context import InstallContext

        source_path = source_node.path
        dest_path = dest_dir / source_path.name

        # Stamp file for ninja tracking
        stamps_dir = self.project.build_dir / ".stamps"
        stamp_name = str(dest_path).replace("/", "_").replace("\\", "_") + ".stamp"
        stamp_path = stamps_dir / stamp_name

        stamp_node = FileNode(stamp_path, defined_at=get_caller_location())
        stamp_node.depends([source_node])

        # Build destination path relative to build directory
        try:
            rel_dest = dest_path.relative_to(self.project.build_dir)
        except ValueError:
            rel_dest = dest_path

        context = InstallContext.from_target(
            target, env, destdir=str(rel_dest).replace("\\", "/")
        )

        stamp_node._build_info = cast(
            BuildInfo,
            {
                "tool": "install",
                "command_var": "copytreecmd",
                "sources": [source_node],
                "depfile": PathToken(
                    path=str(stamp_path), path_type="build", suffix=".d"
                ),
                "deps_style": "gcc",
                "description": "INSTALLDIR $out",
                "context": context,
                "env": env,
            },
        )

        installed_nodes.append(stamp_node)

        canonical = self.project.path_resolver.canonicalize(stamp_path)
        if canonical not in self.project._nodes:
            self.project._nodes[canonical] = stamp_node

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
        dest = path_resolver.normalize_target_path(dest, target_name=target.name)

        source_node = sources[0]

        # Create destination node
        dest_node = FileNode(dest, defined_at=get_caller_location())
        dest_node.depends([source_node])

        # Store build info referencing env.install.copycmd
        env = self._get_install_env(target)
        dest_node._build_info = {
            "tool": "install",
            "command_var": "copycmd",
            "sources": [source_node],
            "description": "INSTALL $out",
            "env": env,
        }

        # Add installed file as output node
        target._install_nodes = [dest_node]
        target.output_nodes.append(dest_node)

        canonical = self.project.path_resolver.canonicalize(dest)
        if canonical not in self.project._nodes:
            self.project._nodes[canonical] = dest_node

    def _create_install_dir_node(
        self, target: Target, sources: list[FileNode], dest_dir: Path
    ) -> None:
        """Create copytree node for InstallDir target."""
        from pcons.tools.archive_context import InstallContext

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
        dest_dir = path_resolver.normalize_target_path(
            dest_dir, target_name=target.name
        )

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

        # Build the destination path relative to build directory for the command
        try:
            rel_dest = dest_path.relative_to(self.project.build_dir)
        except ValueError:
            rel_dest = dest_path

        # Create context from target (merges env defaults with target overrides)
        env = self._get_install_env(target)
        context = InstallContext.from_target(
            target, env, destdir=str(rel_dest).replace("\\", "/")
        )

        # Store build info referencing env.install.copytreecmd
        # The context provides env overrides for command expansion
        # Depfile is PathToken with the stamp path + ".d" suffix
        stamp_node._build_info = cast(
            BuildInfo,
            {
                "tool": "install",
                "command_var": "copytreecmd",
                "sources": [source_node],
                "depfile": PathToken(
                    path=str(stamp_path), path_type="build", suffix=".d"
                ),
                "deps_style": "gcc",
                "description": "INSTALLDIR $out",
                # Context provides get_env_overrides() for template expansion
                "context": context,
                "env": env,
            },
        )

        # Add stamp node as output
        target._install_nodes = [stamp_node]
        target.output_nodes.append(stamp_node)

        canonical = self.project.path_resolver.canonicalize(stamp_path)
        if canonical not in self.project._nodes:
            self.project._nodes[canonical] = stamp_node


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

        Raises:
            BuilderError: If source is a list (use Install() for multiple files).
        """
        # Validate source is not a list - common user error
        if isinstance(source, (list, tuple)):
            from pcons.core.errors import BuilderError

            raise BuilderError(
                "InstallAs() takes a single source, not a list. "
                "Use Install() for multiple files.",
                location=get_caller_location(),
            )

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
