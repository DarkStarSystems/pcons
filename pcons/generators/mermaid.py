# SPDX-License-Identifier: MIT
"""Mermaid diagram generator for dependency visualization.

Generates Mermaid flowchart syntax showing the complete dependency graph.
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
        generator.generate(project, Path("build"))
        # Creates build/deps.mmd
    """

    def __init__(
        self,
        *,
        include_headers: bool = False,
        direction: str = "LR",
        output_filename: str = "deps.mmd",
    ) -> None:
        """Initialize the Mermaid generator.

        Args:
            include_headers: If True, parse .d files to include header
                           dependencies. Requires a prior build.
            direction: Graph direction - "LR" (left-right), "TB" (top-bottom),
                      "RL" (right-left), or "BT" (bottom-top).
            output_filename: Name of the output file.
        """
        super().__init__("mermaid")
        self._include_headers = include_headers
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
            self._write_graph(f, project)

    def _write_header(self, f: TextIO, project: Project) -> None:
        """Write Mermaid header."""
        f.write("---\n")
        f.write(f"title: {project.name} Dependencies\n")
        f.write("---\n")
        f.write(f"flowchart {self._direction}\n")

    def _write_graph(self, f: TextIO, project: Project) -> None:
        """Write the complete dependency graph.

        Shows all files: sources, objects, libraries, programs.
        """
        written_nodes: set[str] = set()
        edges: list[tuple[str, str]] = []

        # Track file names to detect conflicts (same name, different path)
        name_to_paths: dict[str, list[Path]] = {}

        def get_short_id(path: Path) -> str:
            """Get a short but unique ID for a file path."""
            name = path.name
            if name not in name_to_paths:
                name_to_paths[name] = []
            if path not in name_to_paths[name]:
                name_to_paths[name].append(path)

            # If multiple files have the same name, include parent dir
            if len(name_to_paths[name]) > 1:
                return self._sanitize_id(f"{path.parent.name}_{name}")
            return self._sanitize_id(name)

        # First pass: collect all file paths to detect name conflicts
        for target in project.targets:
            for node in target.output_nodes:
                if isinstance(node, FileNode):
                    name = node.path.name
                    if name not in name_to_paths:
                        name_to_paths[name] = []
                    if node.path not in name_to_paths[name]:
                        name_to_paths[name].append(node.path)

            for node in target.object_nodes:
                if isinstance(node, FileNode):
                    name = node.path.name
                    if name not in name_to_paths:
                        name_to_paths[name] = []
                    if node.path not in name_to_paths[name]:
                        name_to_paths[name].append(node.path)

                    for dep in node.explicit_deps:
                        if isinstance(dep, FileNode):
                            name = dep.path.name
                            if name not in name_to_paths:
                                name_to_paths[name] = []
                            if dep.path not in name_to_paths[name]:
                                name_to_paths[name].append(dep.path)

        # Second pass: write nodes and collect edges
        for target in project.targets:
            # Output nodes (libraries, programs)
            for node in target.output_nodes:
                if isinstance(node, FileNode):
                    node_id = get_short_id(node.path)
                    if node_id not in written_nodes:
                        label = node.path.name
                        shape = self._get_output_shape(target)
                        f.write(f"  {node_id}{shape[0]}{label}{shape[1]}\n")
                        written_nodes.add(node_id)

            # Object nodes
            for node in target.object_nodes:
                if isinstance(node, FileNode):
                    node_id = get_short_id(node.path)
                    if node_id not in written_nodes:
                        label = node.path.name
                        f.write(f"  {node_id}({label})\n")  # Rounded for objects
                        written_nodes.add(node_id)

                    # Source dependencies
                    for dep in node.explicit_deps:
                        if isinstance(dep, FileNode):
                            dep_id = get_short_id(dep.path)
                            if dep_id not in written_nodes:
                                dep_label = dep.path.name
                                f.write(
                                    f"  {dep_id}>{dep_label}]\n"
                                )  # Flag for sources
                                written_nodes.add(dep_id)
                            edges.append((dep_id, node_id))

                    # Header dependencies from .d files (if enabled)
                    if self._include_headers:
                        header_deps = self._parse_depfile(node.path)
                        for header in header_deps:
                            header_id = get_short_id(header)
                            if header_id not in written_nodes:
                                f.write(f"  {header_id}>{header.name}]\n")
                                written_nodes.add(header_id)
                            edges.append((header_id, node_id))

            # Edges: objects → outputs
            for output in target.output_nodes:
                if isinstance(output, FileNode):
                    output_id = get_short_id(output.path)
                    for obj in target.object_nodes:
                        if isinstance(obj, FileNode):
                            edges.append((get_short_id(obj.path), output_id))

            # Edges: dependency libraries → this target's output
            for output in target.output_nodes:
                if isinstance(output, FileNode):
                    output_id = get_short_id(output.path)
                    for dep_target in target.dependencies:
                        for dep_output in dep_target.output_nodes:
                            if isinstance(dep_output, FileNode):
                                edges.append((get_short_id(dep_output.path), output_id))

        f.write("\n")

        # Write edges (deduplicated)
        seen_edges: set[tuple[str, str]] = set()
        for src, dst in edges:
            if (src, dst) not in seen_edges:
                f.write(f"  {src} --> {dst}\n")
                seen_edges.add((src, dst))

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
        else:
            return ("[", "]")

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
        """Sanitize a name for use as a Mermaid node ID."""
        result = name.replace("/", "_").replace("\\", "_")
        result = result.replace(".", "_").replace("-", "_")
        result = result.replace(" ", "_").replace(":", "_")
        if result and result[0].isdigit():
            result = "n" + result
        return result
