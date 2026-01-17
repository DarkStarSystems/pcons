# SPDX-License-Identifier: MIT
"""Mermaid diagram generator for dependency visualization.

Generates Mermaid flowchart syntax showing the dependency graph.
Output can be rendered in GitHub markdown, documentation tools,
or the Mermaid live editor (https://mermaid.live).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, TextIO

from pcons.core.node import FileNode
from pcons.generators.generator import BaseGenerator

if TYPE_CHECKING:
    from pcons.core.project import Project
    from pcons.core.target import Target


class MermaidGenerator(BaseGenerator):
    """Generator that produces Mermaid flowchart diagrams.

    Generates a dependency graph visualization in Mermaid syntax.
    Can show target-level dependencies, file-level dependencies, or both.

    Example output:
        ```mermaid
        flowchart LR
          libmath[libmath.a]
          libphysics[libphysics.a]
          app[app]
          libmath --> libphysics
          libphysics --> app
        ```

    Usage:
        generator = MermaidGenerator()
        generator.generate(project, Path("build"))
        # Creates build/deps.mmd
    """

    def __init__(
        self,
        *,
        show_files: bool = False,
        direction: str = "LR",
        output_filename: str = "deps.mmd",
    ) -> None:
        """Initialize the Mermaid generator.

        Args:
            show_files: If True, show file-level dependencies (more detailed).
                       If False, show only target-level dependencies.
            direction: Graph direction - "LR" (left-right), "TB" (top-bottom),
                      "RL" (right-left), or "BT" (bottom-top).
            output_filename: Name of the output file.
        """
        super().__init__("mermaid")
        self._show_files = show_files
        self._direction = direction
        self._output_filename = output_filename

    def generate(self, project: Project, output_dir: Path) -> None:
        """Generate Mermaid diagram file.

        Args:
            project: Configured project to visualize.
            output_dir: Directory to write output file to.
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        output_file = output_dir / self._output_filename

        with open(output_file, "w") as f:
            self._write_header(f, project)

            if self._show_files:
                self._write_file_graph(f, project)
            else:
                self._write_target_graph(f, project)

    def _write_header(self, f: TextIO, project: Project) -> None:
        """Write Mermaid header."""
        f.write(f"---\n")
        f.write(f"title: {project.name} Dependencies\n")
        f.write(f"---\n")
        f.write(f"flowchart {self._direction}\n")

    def _write_target_graph(self, f: TextIO, project: Project) -> None:
        """Write target-level dependency graph."""
        targets = list(project.targets)
        if not targets:
            f.write("  empty[No targets]\n")
            return

        # Define nodes with shapes based on target type
        for target in targets:
            node_id = self._sanitize_id(target.name)
            label = self._get_target_label(target)
            shape = self._get_target_shape(target)
            f.write(f"  {node_id}{shape[0]}{label}{shape[1]}\n")

        f.write("\n")

        # Define edges
        for target in targets:
            target_id = self._sanitize_id(target.name)
            for dep in target.dependencies:
                dep_id = self._sanitize_id(dep.name)
                f.write(f"  {dep_id} --> {target_id}\n")

    def _write_file_graph(self, f: TextIO, project: Project) -> None:
        """Write file-level dependency graph."""
        # Collect all nodes
        written_nodes: set[str] = set()
        edges: list[tuple[str, str]] = []

        # First pass: collect all nodes and edges from targets
        for target in project.targets:
            # Add target output nodes
            for node in target.output_nodes:
                if isinstance(node, FileNode):
                    node_id = self._sanitize_id(str(node.path))
                    if node_id not in written_nodes:
                        label = node.path.name
                        f.write(f"  {node_id}[{label}]\n")
                        written_nodes.add(node_id)

            # Add object nodes
            for node in target.object_nodes:
                if isinstance(node, FileNode):
                    node_id = self._sanitize_id(str(node.path))
                    if node_id not in written_nodes:
                        label = node.path.name
                        f.write(f"  {node_id}({label})\n")  # Rounded for objects
                        written_nodes.add(node_id)

                    # Add source dependencies
                    for dep in node.explicit_deps:
                        if isinstance(dep, FileNode):
                            dep_id = self._sanitize_id(str(dep.path))
                            if dep_id not in written_nodes:
                                dep_label = dep.path.name
                                f.write(f"  {dep_id}>{dep_label}]\n")  # Flag shape for sources
                                written_nodes.add(dep_id)
                            edges.append((dep_id, node_id))

            # Add edges from objects to outputs
            for output in target.output_nodes:
                if isinstance(output, FileNode):
                    output_id = self._sanitize_id(str(output.path))
                    for obj in target.object_nodes:
                        if isinstance(obj, FileNode):
                            obj_id = self._sanitize_id(str(obj.path))
                            edges.append((obj_id, output_id))

        f.write("\n")

        # Write edges
        for src, dst in edges:
            f.write(f"  {src} --> {dst}\n")

    def _get_target_label(self, target: Target) -> str:
        """Get display label for a target."""
        if target.output_nodes:
            # Use the output filename
            for node in target.output_nodes:
                if isinstance(node, FileNode):
                    return node.path.name
        return target.name

    def _get_target_shape(self, target: Target) -> tuple[str, str]:
        """Get Mermaid shape brackets for a target type.

        Returns:
            Tuple of (opening, closing) brackets.
        """
        target_type = getattr(target, "target_type", None)

        if target_type == "program":
            return ("[[", "]]")  # Stadium shape for executables
        elif target_type == "shared_library":
            return ("([", "])")  # Stadium shape
        elif target_type == "static_library":
            return ("[", "]")  # Rectangle
        elif target_type == "interface":
            return ("{{", "}}")  # Hexagon for header-only
        else:
            return ("[", "]")  # Default rectangle

    def _sanitize_id(self, name: str) -> str:
        """Sanitize a name for use as a Mermaid node ID."""
        # Replace problematic characters
        result = name.replace("/", "_").replace("\\", "_")
        result = result.replace(".", "_").replace("-", "_")
        result = result.replace(" ", "_").replace(":", "_")
        # Ensure it starts with a letter
        if result and result[0].isdigit():
            result = "n" + result
        return result
