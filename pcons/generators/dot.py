# SPDX-License-Identifier: MIT
"""GraphViz DOT generator for dependency visualization.

Generates GraphViz DOT format showing the complete dependency graph.
Output can be rendered with `dot`, `neato`, or other GraphViz tools,
or viewed in online tools like https://dreampuf.github.io/GraphvizOnline/.
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


# Muted grey for scanner machinery, green for what a build discovered: both
# read as commentary next to the black-on-white graph proper.
_SCAN_COLOR = "#999999"
_DISCOVERED_COLOR = "#1f7a4d"


class DotGenerator(GraphGenerator):
    """Generator that produces GraphViz DOT diagrams.

    Writes the complete dependency graph — sources, objects, libraries,
    and programs — to <build_dir>/deps.dot by default.
    """

    def __init__(
        self,
        *,
        include_headers: bool = False,
        include_scan: bool = False,
        include_discovered: bool = False,
        rankdir: str = "LR",
        output_filename: str = "deps.dot",
        output_dir: Path | None = None,
    ) -> None:
        """Initialize the DOT generator.

        Args:
            include_headers: If True, parse .d files to include header
                dependencies. Requires a prior build.
            include_scan: If True, draw each scanner's own edges (scan,
                collate, dyndep) instead of eliding them.
            include_discovered: If True, draw the dependencies a previous
                build's dyndep files record. Requires a prior build.
            rankdir: Graph direction - "LR", "TB", "RL", or "BT".
            output_filename: Name of the output file.
            output_dir: Override output directory (default: project.build_dir).
        """
        super().__init__(
            "dot",
            include_headers=include_headers,
            include_scan=include_scan,
            include_discovered=include_discovered,
            output_filename=output_filename,
            output_dir=output_dir,
        )
        self._rankdir = rankdir

    def _write_header(self, f: TextIO, project: Project) -> None:
        """Write DOT header."""
        f.write(f'digraph "{project.name}" {{\n')
        f.write(f"  rankdir={self._rankdir};\n")
        f.write('  node [fontname="Helvetica" fontsize=10];\n')
        f.write('  edge [color="#666666"];\n')
        f.write("\n")

    def _write_footer(self, f: TextIO) -> None:
        """Write DOT footer."""
        f.write("}\n")

    def _nodes_preamble(self) -> str:
        return "  // Nodes\n"

    def _edges_preamble(self) -> str:
        return "\n  // Edges\n"

    def _source_node_line(self, node_id: str, label: str) -> str:
        return f'  {node_id} [label="{label}" shape=note];\n'

    def _output_node_line(
        self, node_id: str, label: str, target: Target, scanned_by: tuple[str, ...] = ()
    ) -> str:
        shape, style = self._get_output_shape(target)
        style_attr = f' style="{style}"' if style else ""
        return (
            f'  {node_id} [label="{self._label(label, scanned_by)}" '
            f"shape={shape}{style_attr}];\n"
        )

    def _object_node_line(
        self, node_id: str, label: str, scanned_by: tuple[str, ...] = ()
    ) -> str:
        return (
            f'  {node_id} [label="{self._label(label, scanned_by)}" shape=ellipse];\n'
        )

    def _header_node_line(self, node_id: str, label: str) -> str:
        return f'  {node_id} [label="{label}" shape=note];\n'

    def _scan_node_line(self, node_id: str, label: str) -> str:
        return (
            f'  {node_id} [label="{label}" shape=folder style="dashed" '
            f'color="{_SCAN_COLOR}" fontcolor="{_SCAN_COLOR}"];\n'
        )

    def _discovered_node_line(self, node_id: str, label: str) -> str:
        return (
            f'  {node_id} [label="{label}" shape=box style="rounded,dashed" '
            f'color="{_DISCOVERED_COLOR}" fontcolor="{_DISCOVERED_COLOR}"];\n'
        )

    def _edge_line(self, src: str, dst: str, style: str = EDGE_PLAIN) -> str:
        if style == EDGE_SCAN:
            return f'  {src} -> {dst} [style=dashed color="{_SCAN_COLOR}"];\n'
        if style == EDGE_DISCOVERED:
            return f'  {src} -> {dst} [style=dashed color="{_DISCOVERED_COLOR}"];\n'
        return f"  {src} -> {dst};\n"

    @staticmethod
    def _label(label: str, scanned_by: tuple[str, ...]) -> str:
        """A node label, with the scanners governing its edge under it.

        A second line, and nothing else: a governed output is a product like
        any other, so it keeps the shape its target type gives it. Dashing it
        would read as "provisional" — the opposite of what it is.

        The backslash-n is DOT's own line break, not Python's: it has to
        reach the file intact.
        """
        if not scanned_by:
            return label
        return label + "\\nscanned: " + ", ".join(sorted(scanned_by))

    def _get_output_shape(self, target: Target) -> tuple[str, str]:
        """Return (shape, style) for an output node based on target type."""
        target_type = getattr(target, "target_type", None)
        if target_type == "program":
            return ("box3d", "")
        elif target_type == "shared_library":
            return ("component", "")
        elif target_type == "static_library":
            return ("box", "")
        elif target_type == "interface":
            return ("hexagon", "")
        elif target_type == "command":
            return ("box", "rounded")
        else:
            return ("box", "")
