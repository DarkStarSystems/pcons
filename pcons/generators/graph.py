# SPDX-License-Identifier: MIT
"""Shared base for dependency-graph generators (DOT, Mermaid).

DotGenerator and MermaidGenerator walk the resolved target graph identically;
only the per-node and per-edge text differs. This base implements the traversal
(`_write_graph`) plus the shared helpers (label resolution, depfile parsing, ID
sanitization) and delegates formatting to abstract `_*_line` hooks.
"""

from __future__ import annotations

from abc import abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, TextIO

from pcons.core.node import FileNode
from pcons.generators.generator import BaseGenerator

if TYPE_CHECKING:
    from pcons.core.project import Project
    from pcons.core.target import Target


class GraphGenerator(BaseGenerator):
    """Base class for dependency-graph visualizers.

    Subclasses provide the file header/footer and the format-specific node and
    edge text; the traversal and helpers live here.
    """

    def __init__(
        self,
        name: str,
        *,
        include_headers: bool,
        output_filename: str,
        output_dir: Path | None,
    ) -> None:
        super().__init__(name)
        self._include_headers = include_headers
        self._output_filename = output_filename
        self._output_dir_override = output_dir

    def _resolve_output_dir(self, project: Project) -> Path:
        """Use the override output_dir if set, otherwise default."""
        if self._output_dir_override is not None:
            return Path(self._output_dir_override)
        return super()._resolve_output_dir(project)

    def _generate_impl(self, project: Project, output_dir: Path) -> None:
        """Generate the diagram file."""
        output_dir.mkdir(parents=True, exist_ok=True)
        output_file = output_dir / self._output_filename

        with open(output_file, "w") as f:
            self._write_header(f, project)
            self._write_graph(f, project)
            self._write_footer(f)

    # --- format-specific hooks (override in subclasses) ---

    @abstractmethod
    def _write_header(self, f: TextIO, project: Project) -> None:
        """Write the file header."""

    def _write_footer(self, f: TextIO) -> None:
        """Write the file footer (none by default)."""

    def _nodes_preamble(self) -> str:
        """Text emitted before the node block (empty by default)."""
        return ""

    def _edges_preamble(self) -> str:
        """Text emitted before the edge block."""
        return "\n"

    @abstractmethod
    def _source_node_line(self, node_id: str, label: str) -> str:
        """Format a source-file node."""

    @abstractmethod
    def _output_node_line(self, node_id: str, label: str, target: Target) -> str:
        """Format an output node (library, program, command output)."""

    @abstractmethod
    def _object_node_line(self, node_id: str, label: str) -> str:
        """Format an intermediate object node."""

    @abstractmethod
    def _header_node_line(self, node_id: str, label: str) -> str:
        """Format a header-dependency node."""

    @abstractmethod
    def _edge_line(self, src: str, dst: str) -> str:
        """Format an edge between two node IDs."""

    # --- shared traversal and helpers ---

    def _get_label(self, path: Path, build_dir: Path, root_dir: Path) -> str:
        """Get a display label for a node path.

        Tries build_dir first, then root_dir, then returns as-is.
        """
        try:
            return str(path.relative_to(build_dir))
        except ValueError:
            pass
        try:
            return str(path.relative_to(root_dir))
        except ValueError:
            return str(path)

    def _write_graph(self, f: TextIO, project: Project) -> None:
        """Write the complete dependency graph.

        Shows all files: sources, objects, libraries, programs.
        """
        build_dir = project.build_dir
        root_dir = project.root_dir
        written_nodes: set[str] = set()
        edges: list[tuple[str, str]] = []

        # Track output node paths and source dep paths for containment edges
        output_node_paths: dict[Path, str] = {}
        source_nodes: dict[Path, str] = {}

        def get_id(path: Path) -> str:
            """Get a unique ID based on the display label."""
            label = self._get_label(path, build_dir, root_dir)
            return self._sanitize_id(label)

        def write_source_node(dep: FileNode) -> str:
            """Write a source dependency node, return its ID."""
            dep_id = get_id(dep.path)
            if dep_id not in written_nodes:
                dep_label = self._get_label(dep.path, build_dir, root_dir)
                f.write(self._source_node_line(dep_id, dep_label))
                written_nodes.add(dep_id)
            source_nodes[dep.path] = dep_id
            return dep_id

        f.write(self._nodes_preamble())
        for target in project.targets:
            # Output nodes (libraries, programs, command outputs)
            for node in target.output_nodes:
                if isinstance(node, FileNode):
                    node_id = get_id(node.path)
                    if node_id not in written_nodes:
                        label = self._get_label(node.path, build_dir, root_dir)
                        f.write(self._output_node_line(node_id, label, target))
                        written_nodes.add(node_id)
                    output_node_paths[node.path] = node_id

                    # Source dependencies directly on output_nodes (for Command targets)
                    for dep in node.explicit_deps:
                        if isinstance(dep, FileNode):
                            dep_id = write_source_node(dep)
                            edges.append((dep_id, node_id))

            # Object nodes
            for node in target.intermediate_nodes:
                if isinstance(node, FileNode):
                    node_id = get_id(node.path)
                    if node_id not in written_nodes:
                        label = self._get_label(node.path, build_dir, root_dir)
                        f.write(self._object_node_line(node_id, label))
                        written_nodes.add(node_id)

                    # Source dependencies
                    for dep in node.explicit_deps:
                        if isinstance(dep, FileNode):
                            dep_id = write_source_node(dep)
                            edges.append((dep_id, node_id))

                    # Header dependencies from .d files (if enabled)
                    if self._include_headers:
                        header_deps = self._parse_depfile(node.path)
                        for header in header_deps:
                            header_id = get_id(header)
                            if header_id not in written_nodes:
                                header_label = self._get_label(
                                    header, build_dir, root_dir
                                )
                                f.write(self._header_node_line(header_id, header_label))
                                written_nodes.add(header_id)
                            edges.append((header_id, node_id))

            # Edges: objects → outputs
            for output in target.output_nodes:
                if isinstance(output, FileNode):
                    output_id = get_id(output.path)
                    for obj in target.intermediate_nodes:
                        if isinstance(obj, FileNode):
                            edges.append((get_id(obj.path), output_id))

            # Edges: dependency libraries → this target's output
            for output in target.output_nodes:
                if isinstance(output, FileNode):
                    output_id = get_id(output.path)
                    for dep_target in target.dependencies:
                        for dep_output in dep_target.output_nodes:
                            if isinstance(dep_output, FileNode):
                                edges.append((get_id(dep_output.path), output_id))

        # Directory containment edges: connect output nodes to source dep
        # nodes when the output path is inside the source path (directory).
        # Normalize paths to labels for comparison since node paths may mix
        # absolute and relative forms.
        source_labels: dict[str, str] = {
            self._get_label(p, build_dir, root_dir): sid
            for p, sid in source_nodes.items()
        }
        output_labels: dict[str, str] = {
            self._get_label(p, build_dir, root_dir): oid
            for p, oid in output_node_paths.items()
        }
        for source_label, source_id in source_labels.items():
            source_lpath = Path(source_label)
            for output_label, output_id in output_labels.items():
                if output_label != source_label:
                    if Path(output_label).is_relative_to(source_lpath):
                        edges.append((output_id, source_id))

        f.write(self._edges_preamble())

        # Write edges (deduplicated)
        seen_edges: set[tuple[str, str]] = set()
        for src, dst in edges:
            if (src, dst) not in seen_edges:
                f.write(self._edge_line(src, dst))
                seen_edges.add((src, dst))

    def _parse_depfile(self, obj_path: Path) -> list[Path]:
        """Parse a .d dependency file to extract header dependencies.

        Args:
            obj_path: Path to the object file (depfile is obj_path + ".d")

        Returns:
            List of header file paths found in the depfile.
        """
        depfile = Path(str(obj_path) + ".d")
        if not depfile.exists():
            return []

        headers: list[Path] = []
        try:
            content = depfile.read_text()
            # GCC/Clang .d format: "target: dep1 dep2 dep3 ..."
            content = content.replace("\\\n", " ")
            if ":" in content:
                deps_part = content.split(":", 1)[1]
                for dep in deps_part.split():
                    dep_path = Path(dep)
                    if dep_path.suffix in (".h", ".hpp", ".hxx", ".H"):
                        dep_str = str(dep_path)
                        if not dep_str.startswith(("/usr", "/Library", "/System")):
                            headers.append(dep_path)
        except (OSError, UnicodeDecodeError):
            pass

        return headers

    def _sanitize_id(self, name: str) -> str:
        """Sanitize a name for use as a graph node ID."""
        result = name.replace("/", "_").replace("\\", "_")
        result = result.replace(".", "_").replace("-", "_")
        result = result.replace(" ", "_").replace(":", "_")
        if result and result[0].isdigit():
            result = "n" + result
        return result
