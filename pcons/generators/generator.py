# SPDX-License-Identifier: MIT
"""Generator protocol for build file generation.

Generators take a configured Project and produce build system files
(e.g., Ninja, Makefiles, IDE project files).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from pcons.core.node import FileNode

if TYPE_CHECKING:
    from pcons.core.project import Project
    from pcons.core.target import Target


@runtime_checkable
class Generator(Protocol):
    """Protocol for build file generators.

    A Generator takes a configured Project and writes build files.
    The output directory is derived from project.build_dir.
    Different generators produce different formats (Ninja, Make,
    IDE projects, etc.).
    """

    @property
    def name(self) -> str:
        """Generator name (e.g., 'ninja', 'make', 'compile_commands')."""
        ...

    def generate(self, project: Project) -> None:
        """Generate build files for a project.

        Args:
            project: The configured project to generate for.
        """
        ...


class BaseGenerator:
    """Base class for generators with common functionality."""

    def __init__(self, name: str) -> None:
        """Initialize a generator.

        Args:
            name: Generator name.
        """
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def generate(self, project: Project) -> None:
        """Generate build files.

        Auto-resolves the project if not already resolved, then
        calls _generate_impl() which subclasses must implement.
        The output directory is computed from project.build_dir.

        Args:
            project: The configured project to generate for.
        """
        if not project._resolved:
            project.resolve()
        output_dir = self._resolve_output_dir(project)
        self._generate_impl(project, output_dir)

    def _resolve_output_dir(self, project: Project) -> Path:
        """Compute the output directory from the project.

        If build_dir is absolute, use it directly; otherwise
        resolve it relative to root_dir.

        Args:
            project: The project to get the output dir for.

        Returns:
            Absolute or resolved output directory path.
        """
        if project.build_dir.is_absolute():
            return project.build_dir
        return project.root_dir / project.build_dir

    def _generate_impl(self, project: Project, output_dir: Path) -> None:
        """Implementation of generate. Subclasses must override."""
        raise NotImplementedError

    def _get_target_build_nodes(self, target: Target) -> list[FileNode]:
        """Get all buildable file nodes from a target.

        This extracts nodes that have build information from resolved targets.

        Args:
            target: The target to get nodes from.

        Returns:
            List of FileNodes that have build information.
        """
        nodes: list[FileNode] = []

        # Add object nodes and output nodes
        for obj_node in target.object_nodes:
            if isinstance(obj_node, FileNode):
                nodes.append(obj_node)
        for out_node in target.output_nodes:
            if isinstance(out_node, FileNode):
                nodes.append(out_node)
        # For interface targets (like Install), also check target.nodes
        if target.target_type == "interface":
            for target_node in target.nodes:
                if isinstance(target_node, FileNode):
                    has_build = getattr(target_node, "_build_info", None) is not None
                    if has_build:
                        nodes.append(target_node)

        return nodes

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.name!r})"
