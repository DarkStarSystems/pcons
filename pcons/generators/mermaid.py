# SPDX-License-Identifier: MIT
"""Mermaid diagram generator for dependency visualization.

Generates Mermaid flowchart syntax showing the complete dependency graph.
Output can be rendered in GitHub markdown, documentation tools,
or the Mermaid live editor (https://mermaid.live).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, TextIO

from pcons.generators.graph import GraphGenerator

if TYPE_CHECKING:
    from pcons.core.project import Project
    from pcons.core.target import Target


class MermaidGenerator(GraphGenerator):
    """Generator that produces Mermaid flowchart diagrams.

    Generates the complete dependency graph showing all files:
    sources, objects, libraries, and programs with their relationships.

    Example output:
        ```mermaid
        flowchart LR
          math_c>math.c]
          math_o(math.o)
          libmath_a[libmath.a]
          main_c>main.c]
          main_o(main.o)
          app[[app]]

          math_c --> math_o
          math_o --> libmath_a
          main_c --> main_o
          main_o --> app
          libmath_a --> app
        ```

    Usage:
        generator = MermaidGenerator()
        generator.generate(project)
        # Creates <build_dir>/deps.mmd

        # Write to a specific directory:
        generator = MermaidGenerator(output_dir=Path("/tmp"))
        generator.generate(project)
        # Creates /tmp/deps.mmd
    """

    def __init__(
        self,
        *,
        include_headers: bool = False,
        direction: str = "LR",
        output_filename: str = "deps.mmd",
        output_dir: Path | None = None,
    ) -> None:
        """Initialize the Mermaid generator.

        Args:
            include_headers: If True, parse .d files to include header
                           dependencies. Requires a prior build.
            direction: Graph direction - "LR" (left-right), "TB" (top-bottom),
                      "RL" (right-left), or "BT" (bottom-top).
            output_filename: Name of the output file.
            output_dir: Override output directory (default: project.build_dir).
        """
        super().__init__(
            "mermaid",
            include_headers=include_headers,
            output_filename=output_filename,
            output_dir=output_dir,
        )
        self._direction = direction

    def _write_header(self, f: TextIO, project: Project) -> None:
        """Write Mermaid header."""
        f.write("---\n")
        f.write(f"title: {project.name} Dependencies\n")
        f.write("---\n")
        f.write(f"flowchart {self._direction}\n")

    def _source_node_line(self, node_id: str, label: str) -> str:
        return f"  {node_id}>{label}]\n"

    def _output_node_line(self, node_id: str, label: str, target: Target) -> str:
        shape = self._get_output_shape(target)
        return f"  {node_id}{shape[0]}{label}{shape[1]}\n"

    def _object_node_line(self, node_id: str, label: str) -> str:
        return f"  {node_id}({label})\n"

    def _header_node_line(self, node_id: str, label: str) -> str:
        return f"  {node_id}>{label}]\n"

    def _edge_line(self, src: str, dst: str) -> str:
        return f"  {src} --> {dst}\n"

    def _get_output_shape(self, target: Target) -> tuple[str, str]:
        """Get Mermaid shape for output node based on target type."""
        target_type = getattr(target, "target_type", None)
        if target_type == "program":
            return ("[[", "]]")  # Stadium for executables
        elif target_type == "shared_library":
            return ("([", "])")  # Stadium
        elif target_type == "static_library":
            return ("[", "]")  # Rectangle
        elif target_type == "interface":
            return ("{{", "}}")  # Hexagon for header-only
        elif target_type == "command":
            return ("([", "])")  # Rounded rectangle for command outputs
        else:
            return ("[", "]")
