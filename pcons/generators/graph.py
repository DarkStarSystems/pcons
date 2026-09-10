# SPDX-License-Identifier: MIT
"""Shared base for dependency-graph generators (DOT, Mermaid).

DotGenerator and MermaidGenerator walk the resolved target graph identically;
only the per-node and per-edge text differs. This base implements the traversal
(`_write_graph`) plus the shared helpers (label resolution, depfile parsing, ID
sanitization) and delegates formatting to abstract `_*_line` hooks.

Three kinds of detail are off by default, each answering a question the plain
graph can't:

- ``include_headers`` parses the compiler's ``.d`` files for header edges.
- ``include_scan`` draws a scanner's machinery — scan edges, collate, dyndep,
  exports. Without it the machinery is elided and the edges it governs are
  simply marked as scanned, because a scope costs several nodes and none of
  them is a file the user asked to build.
- ``include_discovered`` reads back the dyndep files a build has written and
  draws what the scan actually discovered.

The last two need a build to have run, as ``include_headers`` does; before
that the files they read don't exist and they draw nothing.
"""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, TextIO

from pcons.core.node import FileNode
from pcons.generators.generator import BaseGenerator

if TYPE_CHECKING:
    from pcons.core.project import Project
    from pcons.core.target import Target

# Edge styles a subclass renders differently. "" is a real graph edge; the
# others are commentary on it.
EDGE_PLAIN = ""
EDGE_SCAN = "scan"
EDGE_DISCOVERED = "discovered"


def label_for(path: Path, build_dir: Path, root_dir: Path) -> str:
    """Display label for a node path: relative to the build or the source dir."""
    try:
        return str(path.relative_to(build_dir))
    except ValueError:
        pass
    try:
        return str(path.relative_to(root_dir))
    except ValueError:
        return str(path)


def sanitize_id(name: str) -> str:
    """Sanitize a name for use as a graph node ID."""
    result = name.replace("/", "_").replace("\\", "_")
    result = result.replace(".", "_").replace("-", "_")
    result = result.replace(" ", "_").replace(":", "_")
    if result and result[0].isdigit():
        result = "n" + result
    return result


@dataclass
class _Emitter:
    """Where the traversal accumulates its output.

    Nodes stream out as they are met (each written once, keyed by ID); edges
    collect here and are written after every node, since a format may need
    the nodes declared first and an edge may be found before its endpoints.
    """

    f: TextIO
    build_dir: Path
    root_dir: Path
    written: set[str] = field(default_factory=set)
    edges: list[tuple[str, str, str]] = field(default_factory=list)

    def label(self, path: Path) -> str:
        return label_for(path, self.build_dir, self.root_dir)

    def node(self, label: str, line: Callable[[str, str], str]) -> str:
        """Write a node for *label* if it is new; return its ID either way."""
        node_id = sanitize_id(label)
        if node_id not in self.written:
            self.f.write(line(node_id, label))
            self.written.add(node_id)
        return node_id

    def path_node(self, path: Path, line: Callable[[str, str], str]) -> str:
        """The same, for a node identified by path."""
        return self.node(self.label(path), line)

    def edge(self, src: str, dst: str, style: str = EDGE_PLAIN) -> None:
        self.edges.append((src, dst, style))


@dataclass
class _ScanView:
    """What the resolved scanners contribute to the picture.

    ``plumbing`` is every file a scanner's own edges write or read on the
    side (scan infos, manifests, dyndep, exports, argument files). The
    traversal skips those nodes: either they are elided, or the scan section
    draws them properly, with their producers.

    ``consumers`` collects who reads one of them, since a skipped node's
    edges are skipped with it and the scan section has to put them back.
    """

    scopes: list[Any] = field(default_factory=list)
    plumbing: set[Path] = field(default_factory=set)
    scanned_by: dict[Path, tuple[str, ...]] = field(default_factory=dict)
    consumers: list[tuple[Path, str]] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.scopes)


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
        include_scan: bool = False,
        include_discovered: bool = False,
        output_filename: str,
        output_dir: Path | None,
    ) -> None:
        super().__init__(name)
        self._include_headers = include_headers
        self._include_scan = include_scan
        self._include_discovered = include_discovered
        self._output_filename = output_filename
        self._output_dir_override = output_dir

    def _resolve_output_dir(self, project: Project) -> Path:
        """Use the override output_dir if set, otherwise default."""
        if self._output_dir_override is not None:
            return Path(self._output_dir_override)
        return super()._resolve_output_dir(project)

    def write(self, project: Project, stream: TextIO) -> None:
        """Write the diagram for a resolved project to an open text stream.

        The synchronous counterpart of ``generate()``, which only queues the
        write. Callers that already hold a resolved project and a destination
        (stdout, say) use this instead.
        """
        self._write_header(stream, project)
        self._write_graph(stream, project)
        self._write_footer(stream)

    def _generate_impl(self, project: Project, output_dir: Path) -> None:
        """Generate the diagram file."""
        output_dir.mkdir(parents=True, exist_ok=True)
        output_file = output_dir / self._output_filename

        with open(output_file, "w", encoding="utf-8") as f:
            self.write(project, f)

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
    def _output_node_line(
        self, node_id: str, label: str, target: Target, scanned_by: tuple[str, ...] = ()
    ) -> str:
        """Format an output node (library, program, command output)."""

    @abstractmethod
    def _object_node_line(
        self, node_id: str, label: str, scanned_by: tuple[str, ...] = ()
    ) -> str:
        """Format an intermediate object node."""

    @abstractmethod
    def _header_node_line(self, node_id: str, label: str) -> str:
        """Format a header-dependency node."""

    @abstractmethod
    def _scan_node_line(self, node_id: str, label: str) -> str:
        """Format a scanner-machinery node (scan info, manifest, dyndep)."""

    @abstractmethod
    def _discovered_node_line(self, node_id: str, label: str) -> str:
        """Format a node the build discovered rather than the script declaring."""

    @abstractmethod
    def _edge_line(self, src: str, dst: str, style: str = EDGE_PLAIN) -> str:
        """Format an edge between two node IDs."""

    # --- shared traversal and helpers ---

    def _get_label(self, path: Path, build_dir: Path, root_dir: Path) -> str:
        """Get a display label for a node path.

        Tries build_dir first, then root_dir, then returns as-is.
        """
        return label_for(path, build_dir, root_dir)

    def _write_graph(self, f: TextIO, project: Project) -> None:
        """Write the complete dependency graph.

        Shows all files: sources, objects, libraries, programs.
        """
        ctx = _Emitter(f=f, build_dir=project.build_dir, root_dir=project.root_dir)
        scan = self._scan_view(project)

        # Track output node paths and source dep paths for containment edges
        output_node_paths: dict[Path, str] = {}
        source_nodes: dict[Path, str] = {}

        def write_dep_node(dep: FileNode) -> str:
            """Write a node for something an edge reads, return its ID.

            A file with a producer is not a source, even when no target owns
            it: a scanner's dyndep file, say, is written by a build edge of
            its own. Calling it a source would draw the graph a lie.
            """
            if dep._build_info is not None:
                return ctx.path_node(dep.path, self._object_node_line)
            dep_id = ctx.path_node(dep.path, self._source_node_line)
            source_nodes[dep.path] = dep_id
            return dep_id

        f.write(self._nodes_preamble())
        for target in project.targets:
            # Output nodes (libraries, programs, command outputs)
            for node in target.output_nodes:
                if isinstance(node, FileNode):
                    scanned_by = scan.scanned_by.get(node.path, ())
                    node_id = ctx.path_node(
                        node.path,
                        lambda i, la, t=target, s=scanned_by: self._output_node_line(
                            i, la, t, s
                        ),
                    )
                    output_node_paths[node.path] = node_id

                    # Source dependencies directly on output_nodes (for Command targets)
                    for dep in node.deps:
                        if not isinstance(dep, FileNode):
                            continue
                        if dep.path in scan.plumbing:
                            scan.consumers.append((dep.path, node_id))
                        else:
                            ctx.edge(write_dep_node(dep), node_id)

            # Object nodes
            for node in target.intermediate_nodes:
                if isinstance(node, FileNode):
                    scanned_by = scan.scanned_by.get(node.path, ())
                    node_id = ctx.path_node(
                        node.path,
                        lambda i, la, s=scanned_by: self._object_node_line(i, la, s),
                    )

                    # Source dependencies. Both kinds: an implicit dep is
                    # still an edge in the graph the user asked to see.
                    for dep in node.deps:
                        if not isinstance(dep, FileNode):
                            continue
                        if dep.path in scan.plumbing:
                            scan.consumers.append((dep.path, node_id))
                        else:
                            ctx.edge(write_dep_node(dep), node_id)

                    # Header dependencies from .d files (if enabled)
                    if self._include_headers:
                        for header in self._parse_depfile(node.path):
                            header_id = ctx.path_node(header, self._header_node_line)
                            ctx.edge(header_id, node_id)

            # Edges: objects → outputs
            for output in target.output_nodes:
                if isinstance(output, FileNode):
                    output_id = sanitize_id(ctx.label(output.path))
                    for obj in target.intermediate_nodes:
                        if isinstance(obj, FileNode):
                            ctx.edge(sanitize_id(ctx.label(obj.path)), output_id)

            # Edges: dependency libraries → this target's output
            for output in target.output_nodes:
                if isinstance(output, FileNode):
                    output_id = sanitize_id(ctx.label(output.path))
                    for dep_target in target.dependencies:
                        for dep_output in dep_target.output_nodes:
                            if isinstance(dep_output, FileNode):
                                ctx.edge(
                                    sanitize_id(ctx.label(dep_output.path)), output_id
                                )

        if scan and self._include_scan:
            self._write_scan_section(ctx, scan)
        if scan and self._include_discovered:
            self._write_discovered_section(ctx, scan, project)

        # Directory containment edges: connect output nodes to source dep
        # nodes when the output path is inside the source path (directory).
        # Normalize paths to labels for comparison since node paths may mix
        # absolute and relative forms.
        source_labels: dict[str, str] = {
            ctx.label(p): sid for p, sid in source_nodes.items()
        }
        output_labels: dict[str, str] = {
            ctx.label(p): oid for p, oid in output_node_paths.items()
        }
        for source_label, source_id in source_labels.items():
            source_lpath = Path(source_label)
            for output_label, output_id in output_labels.items():
                if output_label != source_label:
                    if Path(output_label).is_relative_to(source_lpath):
                        ctx.edge(output_id, source_id)

        f.write(self._edges_preamble())

        # Write edges (deduplicated)
        seen_edges: set[tuple[str, str, str]] = set()
        for src, dst, style in ctx.edges:
            if (src, dst, style) not in seen_edges:
                f.write(self._edge_line(src, dst, style))
                seen_edges.add((src, dst, style))

    # --- scanners: discovered dependencies ---

    def _scan_view(self, project: Project) -> _ScanView:
        """Summarize the project's scan scopes for the traversal.

        Reads what the wiring pass recorded (``project._scan_scopes``) rather
        than re-deriving it: the scopes already know their governed edges,
        their scan infos, and the collate edge that writes the dyndep file.
        """
        view = _ScanView()
        for scope in getattr(project, "_scan_scopes", {}).values():
            collate = scope.collate_node
            if collate is None:  # a pass-through scope owns no files
                continue
            view.scopes.append(scope)
            view.plumbing.add(collate.path)
            view.plumbing.update(node.path for node in scope.info_nodes)
            view.plumbing.update(
                dep.path for dep in collate.deps if isinstance(dep, FileNode)
            )
            for spec in self._collate_outputs(scope):
                view.plumbing.add(spec)
            for governed in scope.governed:
                names = view.scanned_by.get(governed.path, ())
                if scope.scanner.name not in names:
                    view.scanned_by[governed.path] = names + (scope.scanner.name,)
        return view

    @staticmethod
    def _collate_outputs(scope: Any) -> list[Path]:
        """The paths one collate edge writes besides the dyndep file."""
        info = scope.collate_node._build_info or {}
        return [
            Path(spec["path"])
            for key, spec in (info.get("outputs") or {}).items()
            if key != "dyndep" and isinstance(spec, dict) and "path" in spec
        ]

    @staticmethod
    def _edge_args_files(scope: Any) -> dict[int, Path]:
        """The args file the collate writes for each governed edge, by index.

        The collate names them ``args_<i>`` over the same edge order the
        scope's ``governed`` list keeps.
        """
        info = scope.collate_node._build_info or {}
        files: dict[int, Path] = {}
        for key, spec in (info.get("outputs") or {}).items():
            if key.startswith("args_") and isinstance(spec, dict) and "path" in spec:
                files[int(key[len("args_") :])] = Path(spec["path"])
        return files

    def _write_scan_section(self, ctx: _Emitter, scan: _ScanView) -> None:
        """Draw each scan scope: sources → scan info → collate → dyndep.

        Everything here is machinery, so it all gets the scan node and edge
        styles; what the machinery *discovered* is a separate section.
        """
        for scope in scan.scopes:
            collate = scope.collate_node
            for node in [*scope.info_nodes, collate]:
                node_id = ctx.path_node(node.path, self._scan_node_line)
                for dep in node.deps:
                    if isinstance(dep, FileNode):
                        if dep.path in scan.plumbing:
                            line = self._scan_node_line
                        elif dep._build_info is not None:
                            line = self._object_node_line
                        else:
                            line = self._source_node_line
                        ctx.edge(ctx.path_node(dep.path, line), node_id, EDGE_SCAN)
            collate_id = sanitize_id(ctx.label(collate.path))
            for path in self._collate_outputs(scope):
                ctx.edge(
                    collate_id,
                    ctx.path_node(path, self._scan_node_line),
                    EDGE_SCAN,
                )
            args_files = self._edge_args_files(scope)
            for index, governed in enumerate(scope.governed):
                governed_id = sanitize_id(ctx.label(governed.path))
                ctx.edge(collate_id, governed_id, EDGE_SCAN)
                # The args file is read by the governed command, which names
                # it through a per-edge variable rather than a dependency, so
                # nothing in the node graph would show it being used. Its
                # path comes from the collate edge that writes it, not from
                # re-deriving the naming rule: one source of truth, and a
                # missing edge rather than a phantom file if that changes.
                args_path = args_files.get(index)
                if args_path is not None:
                    ctx.edge(
                        ctx.path_node(args_path, self._scan_node_line),
                        governed_id,
                        EDGE_SCAN,
                    )

        # Whoever reads a scanner's file: the link that reads a link-args
        # file, say. The traversal skipped these along with the node.
        for path, consumer_id in scan.consumers:
            ctx.edge(ctx.path_node(path, self._scan_node_line), consumer_id, EDGE_SCAN)

    def _write_discovered_section(
        self, ctx: _Emitter, scan: _ScanView, project: Project
    ) -> None:
        """Draw what a previous build's dyndep files actually discovered.

        The point of a scanner is that these edges are unknowable at configure
        time, so they can only be read back from the build — like the header
        edges of ``include_headers``. Nothing built yet, nothing drawn.
        """
        from pcons.core.collate import read_dyndep_entries

        build_dir = project.build_dir
        if not build_dir.is_absolute():
            build_dir = project.root_dir / build_dir
        for scope in scan.scopes:
            if scope.dyndep_rel is None:
                continue
            for out, provides, requires in read_dyndep_entries(
                build_dir / scope.dyndep_rel
            ):
                # Paths in a dyndep file are relative to the build directory,
                # which is exactly how this graph labels a built file.
                out_id = ctx.node(out, self._discovered_node_line)
                for provide in provides:
                    ctx.edge(
                        out_id,
                        ctx.node(provide, self._discovered_node_line),
                        EDGE_DISCOVERED,
                    )
                for require in requires:
                    ctx.edge(
                        ctx.node(require, self._discovered_node_line),
                        out_id,
                        EDGE_DISCOVERED,
                    )

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
            content = depfile.read_text(encoding="utf-8")
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
        return sanitize_id(name)
