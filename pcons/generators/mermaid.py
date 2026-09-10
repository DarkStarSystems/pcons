# SPDX-License-Identifier: MIT
"""Mermaid diagram generator for dependency visualization.

Generates Mermaid flowchart syntax showing the complete dependency graph.
Output can be rendered in GitHub markdown, documentation tools,
or the Mermaid live editor (https://mermaid.live).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, TextIO

from pcons.generators.graph import (
    EDGE_DISCOVERED,
    EDGE_PLAIN,
    EDGE_SCAN,
    GraphGenerator,
)

if TYPE_CHECKING:
    from pcons.core.project import Project
    from pcons.core.target import Target


# Matches the DOT generator's palette: grey machinery, green discovery.
_SCAN_COLOR = "#999999"
_DISCOVERED_COLOR = "#1f7a4d"


class MermaidGenerator(GraphGenerator):
    """Generator that produces Mermaid flowchart diagrams.

    Writes the complete dependency graph — sources, objects, libraries,
    and programs — to <build_dir>/deps.mmd by default.
    """

    def __init__(
        self,
        *,
        include_headers: bool = False,
        include_scan: bool = False,
        include_discovered: bool = False,
        direction: str = "LR",
        output_filename: str = "deps.mmd",
        output_dir: Path | None = None,
    ) -> None:
        """Initialize the Mermaid generator.

        Args:
            include_headers: If True, parse .d files to include header
                dependencies. Requires a prior build.
            include_scan: If True, draw each scanner's own edges (scan,
                collate, dyndep) instead of eliding them.
            include_discovered: If True, draw the dependencies a previous
                build's dyndep files record. Requires a prior build.
            direction: Graph direction - "LR", "TB", "RL", or "BT".
            output_filename: Name of the output file.
            output_dir: Override output directory (default: project.build_dir).
        """
        super().__init__(
            "mermaid",
            include_headers=include_headers,
            include_scan=include_scan,
            include_discovered=include_discovered,
            output_filename=output_filename,
            output_dir=output_dir,
        )
        self._direction = direction

    def _write_header(self, f: TextIO, project: Project) -> None:
        """Write Mermaid header, including the classes the nodes use."""
        f.write("---\n")
        f.write(f"title: {project.name} Dependencies\n")
        f.write("---\n")
        f.write(f"flowchart {self._direction}\n")
        f.write(
            f"  classDef scan stroke-dasharray: 4 3,stroke:{_SCAN_COLOR},"
            f"color:{_SCAN_COLOR}\n"
        )
        f.write(
            f"  classDef discovered stroke-dasharray: 4 3,"
            f"stroke:{_DISCOVERED_COLOR},color:{_DISCOVERED_COLOR}\n"
        )

    def _source_node_line(self, node_id: str, label: str) -> str:
        return f"  {node_id}>{label}]\n"

    def _output_node_line(
        self, node_id: str, label: str, target: Target, scanned_by: tuple[str, ...] = ()
    ) -> str:
        shape = self._get_output_shape(target)
        return f"  {node_id}{shape[0]}{self._label(label, scanned_by)}{shape[1]}\n"

    def _object_node_line(
        self, node_id: str, label: str, scanned_by: tuple[str, ...] = ()
    ) -> str:
        return f"  {node_id}({self._label(label, scanned_by)})\n"

    def _header_node_line(self, node_id: str, label: str) -> str:
        return f"  {node_id}>{label}]\n"

    def _scan_node_line(self, node_id: str, label: str) -> str:
        # Quoted: these paths carry slashes, which would otherwise run into
        # the parallelogram's own delimiters.
        return f'  {node_id}[/"{label}"/]:::scan\n'

    def _discovered_node_line(self, node_id: str, label: str) -> str:
        return f'  {node_id}("{label}"):::discovered\n'

    def _edge_line(self, src: str, dst: str, style: str = EDGE_PLAIN) -> str:
        if style == EDGE_SCAN:
            return f"  {src} -.-> {dst}\n"
        if style == EDGE_DISCOVERED:
            return f"  {src} -. discovered .-> {dst}\n"
        return f"  {src} --> {dst}\n"

    @staticmethod
    def _label(label: str, scanned_by: tuple[str, ...]) -> str:
        """A node label, with the scanners governing its edge under it.

        A second line, and nothing else: a governed output is a product like
        any other and keeps the shape its target type gives it.
        """
        if not scanned_by:
            return label
        return f'"{label}<br>scanned: {", ".join(sorted(scanned_by))}"'

    def _get_output_shape(self, target: Target) -> tuple[str, str]:
        """Get Mermaid shape for output node based on target type."""
        target_type = getattr(target, "target_type", None)
        if target_type == "program":
            return ("[[", "]]")
        elif target_type == "shared_library":
            return ("([", "])")
        elif target_type == "static_library":
            return ("[", "]")
        elif target_type == "interface":
            return ("{{", "}}")
        elif target_type == "command":
            return ("([", "])")
        else:
            return ("[", "]")
