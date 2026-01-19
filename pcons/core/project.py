# SPDX-License-Identifier: MIT
"""Project container for pcons builds.

The Project is the top-level container that holds all environments,
targets, and nodes for a build. It provides node deduplication and
serves as the context for build descriptions.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pcons.core.environment import Environment as Env
from pcons.core.graph import (
    collect_all_nodes,
    detect_cycles_in_targets,
    topological_sort_targets,
)
from pcons.core.node import AliasNode, DirNode, FileNode, Node
from pcons.core.target import Target, TargetType
from pcons.util.source_location import SourceLocation, get_caller_location

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from pcons.tools.toolchain import Toolchain


class Project:
    """Top-level container for a pcons build.

    The Project manages:
    - Environments for different build configurations
    - Targets (libraries, programs, etc.)
    - Node deduplication (same path → same node)
    - Default targets for 'ninja' with no arguments
    - Build validation (cycle detection, missing sources)

    Example:
        project = Project("myproject")

        # Create environment with toolchain
        env = project.Environment(toolchain=gcc)

        # Create targets
        lib = project.Library("mylib", env, sources=["lib.cpp"])
        app = project.Program("app", env, sources=["main.cpp"])
        app.link(lib)

        # Set defaults
        project.Default(app)

    Attributes:
        name: Project name.
        root_dir: Project root directory.
        build_dir: Directory for build outputs.
        config: Cached configuration (from configure phase).
    """

    __slots__ = (
        "name",
        "root_dir",
        "build_dir",
        "_environments",
        "_targets",
        "_nodes",
        "_aliases",
        "_default_targets",
        "_config",
        "defined_at",
    )

    def __init__(
        self,
        name: str,
        *,
        root_dir: Path | str | None = None,
        build_dir: Path | str = "build",
        config: Any = None,
        defined_at: SourceLocation | None = None,
    ) -> None:
        """Create a project.

        Args:
            name: Project name.
            root_dir: Project root directory (default: current dir).
            build_dir: Directory for build outputs (default: "build").
            config: Cached configuration from configure phase.
            defined_at: Source location where project was created.
        """
        self.name = name
        self.root_dir = Path(root_dir) if root_dir else Path.cwd()
        self.build_dir = Path(build_dir)
        self._environments: list[Env] = []
        self._targets: dict[str, Target] = {}
        self._nodes: dict[Path, Node] = {}
        self._aliases: dict[str, AliasNode] = {}
        self._default_targets: list[Target] = []
        self._config = config
        self.defined_at = defined_at or get_caller_location()

        # Auto-register with global registry (for CLI access)
        from pcons import _register_project

        _register_project(self)

    @property
    def config(self) -> Any:
        """Get the cached configuration."""
        return self._config

    @config.setter
    def config(self, value: Any) -> None:
        """Set the cached configuration."""
        self._config = value

    def Environment(
        self,
        toolchain: Toolchain | None = None,
        name: str | None = None,
        **kwargs: Any,
    ) -> Env:
        """Create and register a new environment.

        Args:
            toolchain: Optional toolchain to initialize with.
            name: Optional name for this environment (used in ninja rule names).
            **kwargs: Additional variables to set on the environment.

        Returns:
            A new Environment attached to this project.
        """
        env = Env(
            name=name,
            toolchain=toolchain,
            defined_at=get_caller_location(),
        )
        env._project = self

        # Set any extra variables
        for key, value in kwargs.items():
            setattr(env, key, value)

        # Set build_dir from project
        env.build_dir = self.build_dir

        self._environments.append(env)
        return env

    def node(self, path: Path | str) -> FileNode:
        """Get or create a file node for a path.

        This provides node deduplication - the same path always
        returns the same node instance.

        Args:
            path: Path to the file.

        Returns:
            FileNode for the path.
        """
        path = Path(path)
        if path not in self._nodes:
            self._nodes[path] = FileNode(path, defined_at=get_caller_location())
        node = self._nodes[path]
        if not isinstance(node, FileNode):
            raise TypeError(
                f"Path {path} is registered as {type(node).__name__}, not FileNode"
            )
        return node

    def dir_node(self, path: Path | str) -> DirNode:
        """Get or create a directory node for a path.

        Args:
            path: Path to the directory.

        Returns:
            DirNode for the path.
        """
        path = Path(path)
        if path not in self._nodes:
            self._nodes[path] = DirNode(path, defined_at=get_caller_location())
        node = self._nodes[path]
        if not isinstance(node, DirNode):
            raise TypeError(
                f"Path {path} is registered as {type(node).__name__}, not DirNode"
            )
        return node

    def add_target(self, target: Target) -> None:
        """Register a target with the project.

        Args:
            target: Target to register.

        Raises:
            ValueError: If a target with the same name already exists.
        """
        if target.name in self._targets:
            existing = self._targets[target.name]
            raise ValueError(
                f"Target '{target.name}' already exists "
                f"(defined at {existing.defined_at})"
            )
        self._targets[target.name] = target

    def get_target(self, name: str) -> Target | None:
        """Get a target by name.

        Args:
            name: Target name.

        Returns:
            The target, or None if not found.
        """
        return self._targets.get(name)

    @property
    def targets(self) -> list[Target]:
        """Get all registered targets."""
        return list(self._targets.values())

    @property
    def environments(self) -> list[Env]:
        """Get all registered environments."""
        return list(self._environments)

    def Alias(self, name: str, *targets: Target | Node) -> AliasNode:
        """Create a named alias for targets.

        Aliases can be used as build targets (e.g., 'ninja test').

        Args:
            name: Alias name.
            *targets: Targets or nodes to include in the alias.

        Returns:
            AliasNode for this alias.
        """
        if name not in self._aliases:
            self._aliases[name] = AliasNode(name, defined_at=get_caller_location())

        alias = self._aliases[name]
        for t in targets:
            if isinstance(t, Target):
                # Use output_nodes (populated after resolve()) or fall back to nodes
                nodes = t.output_nodes if t.output_nodes else t.nodes
                alias.add_targets(nodes)
            else:
                alias.add_target(t)

        return alias

    def Default(self, *targets: Target | Node | str) -> None:
        """Set default targets for building.

        These are built when 'ninja' is run with no arguments.

        Args:
            *targets: Targets, nodes, or alias names to build by default.
        """
        for t in targets:
            if isinstance(t, Target):
                if t not in self._default_targets:
                    self._default_targets.append(t)
            elif isinstance(t, str):
                # Look up by name
                target = self._targets.get(t)
                if target and target not in self._default_targets:
                    self._default_targets.append(target)

    @property
    def default_targets(self) -> list[Target]:
        """Get the default build targets."""
        return list(self._default_targets)

    @property
    def aliases(self) -> dict[str, AliasNode]:
        """Get all defined aliases."""
        return dict(self._aliases)

    def all_nodes(self) -> set[Node]:
        """Collect all nodes from all targets."""
        return collect_all_nodes(list(self._targets.values()))

    def validate(self) -> list[Exception]:
        """Validate the project configuration.

        Checks for:
        - Dependency cycles
        - Missing source files
        - Undefined targets referenced as dependencies

        Returns:
            List of validation errors (empty if valid).
        """
        errors: list[Exception] = []

        # Check for dependency cycles
        cycles = detect_cycles_in_targets(list(self._targets.values()))
        for cycle in cycles:
            from pcons.core.errors import DependencyCycleError

            errors.append(DependencyCycleError(cycle))

        # Check for missing sources
        from pcons.core.errors import MissingSourceError

        for target in self._targets.values():
            for source in target.sources:
                if isinstance(source, FileNode):
                    # Only check source files (not generated files)
                    if source.builder is None and not source.exists():
                        errors.append(MissingSourceError(str(source.path)))

        return errors

    def build_order(self) -> list[Target]:
        """Get targets in the order they should be built.

        Returns:
            Targets sorted so dependencies come before dependents.
        """
        return topological_sort_targets(list(self._targets.values()))

    def dump_graph(self, path: Path | str | None = None) -> str:
        """Dump the dependency graph in Graphviz DOT format.

        Shows all files: sources, objects, libraries, and programs with
        their relationships. Call this after resolve() for complete information.

        Args:
            path: Optional file path to write the DOT file.
                  If None, just returns the DOT string.

        Returns:
            The DOT format string.

        Example:
            project.resolve()
            project.dump_graph("build/graph.dot")
            # Then: dot -Tpng build/graph.dot -o build/graph.png
        """
        lines = [
            f"// Dependency graph for {self.name}",
            "digraph pcons {",
            "  rankdir=LR;",
            "  node [shape=box];",
            "",
        ]

        written_nodes: set[str] = set()
        edges: list[tuple[str, str]] = []

        # Track file names to detect conflicts (same name, different path)
        name_to_paths: dict[str, list[Path]] = {}

        def sanitize_id(name: str) -> str:
            """Sanitize a name for use as a DOT node ID."""
            result = name.replace("/", "_").replace("\\", "_")
            result = result.replace(".", "_").replace("-", "_")
            result = result.replace(" ", "_").replace(":", "_")
            if result and result[0].isdigit():
                result = "n" + result
            return result

        def get_short_id(file_path: Path) -> str:
            """Get a short but unique ID for a file path."""
            name = file_path.name
            if name not in name_to_paths:
                name_to_paths[name] = []
            if file_path not in name_to_paths[name]:
                name_to_paths[name].append(file_path)

            # If multiple files have the same name, include parent dir
            if len(name_to_paths[name]) > 1:
                return sanitize_id(f"{file_path.parent.name}_{name}")
            return sanitize_id(name)

        # First pass: collect all file paths to detect name conflicts
        for target in self._targets.values():
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

        # Node styles by type
        def get_node_style(target: Target) -> tuple[str, str]:
            """Get DOT style for output node based on target type."""
            target_type_str = (
                str(target.target_type) if target.target_type else "unknown"
            )
            styles = {
                "program": ("shape=doubleoctagon", "lightyellow"),
                "shared_library": ("shape=box3d", "lightgreen"),
                "static_library": ("shape=box", "lightblue"),
                "interface": ("shape=hexagon", "lightgray"),
                "object": ("shape=ellipse", "white"),
            }
            shape, color = styles.get(target_type_str, ("shape=box", "white"))
            return shape, color

        # Second pass: write nodes and collect edges
        lines.append("  // Source files")
        for target in self._targets.values():
            for node in target.object_nodes:
                if isinstance(node, FileNode):
                    for dep in node.explicit_deps:
                        if isinstance(dep, FileNode):
                            dep_id = get_short_id(dep.path)
                            if dep_id not in written_nodes:
                                label = dep.path.name
                                lines.append(
                                    f'  {dep_id} [label="{label}", '
                                    f"shape=note, fillcolor=white, style=filled];"
                                )
                                written_nodes.add(dep_id)

        lines.append("")
        lines.append("  // Object files")
        for target in self._targets.values():
            for node in target.object_nodes:
                if isinstance(node, FileNode):
                    node_id = get_short_id(node.path)
                    if node_id not in written_nodes:
                        label = node.path.name
                        lines.append(
                            f'  {node_id} [label="{label}", '
                            f"shape=ellipse, fillcolor=wheat, style=filled];"
                        )
                        written_nodes.add(node_id)

                    # Source dependencies
                    for dep in node.explicit_deps:
                        if isinstance(dep, FileNode):
                            dep_id = get_short_id(dep.path)
                            edges.append((dep_id, node_id))

        lines.append("")
        lines.append("  // Output files (libraries, programs)")
        for target in self._targets.values():
            # Skip interface targets (Install, etc.) - they're just copy operations
            # and don't represent build dependencies
            target_type_str = (
                str(target.target_type) if target.target_type else "unknown"
            )
            if target_type_str == "interface":
                continue

            shape, color = get_node_style(target)
            for node in target.output_nodes:
                if isinstance(node, FileNode):
                    node_id = get_short_id(node.path)
                    if node_id not in written_nodes:
                        label = node.path.name
                        lines.append(
                            f'  {node_id} [label="{label}", '
                            f"{shape}, fillcolor={color}, style=filled];"
                        )
                        written_nodes.add(node_id)

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
                        # Skip interface dependencies too
                        dep_type_str = (
                            str(dep_target.target_type)
                            if dep_target.target_type
                            else "unknown"
                        )
                        if dep_type_str == "interface":
                            continue
                        for dep_output in dep_target.output_nodes:
                            if isinstance(dep_output, FileNode):
                                edges.append((get_short_id(dep_output.path), output_id))

        lines.append("")
        lines.append("  // Edges")

        # Write edges (deduplicated)
        seen_edges: set[tuple[str, str]] = set()
        for src, dst in edges:
            if (src, dst) not in seen_edges:
                lines.append(f"  {src} -> {dst};")
                seen_edges.add((src, dst))

        lines.append("}")

        dot_str = "\n".join(lines)

        if path:
            path = Path(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(dot_str)
            logger.info("Wrote dependency graph to %s", path)

        return dot_str

    def print_targets(self) -> None:
        """Print a human-readable summary of all targets.

        Useful for debugging. Shows target names, types, and dependencies.
        """
        print(f"Project: {self.name}")
        print(f"Build dir: {self.build_dir}")
        print(f"Targets ({len(self._targets)}):")

        for name, target in sorted(self._targets.items()):
            print(f"  {name} ({target.target_type})")
            if target.sources:
                print(f"    sources: {len(target.sources)} files")
            if target.output_nodes:
                for node in target.output_nodes[:3]:
                    print(f"    output: {node.path}")
                if len(target.output_nodes) > 3:
                    print(f"    ... and {len(target.output_nodes) - 3} more")
            if target.dependencies:
                deps = [
                    d.name if hasattr(d, "name") else str(d)
                    for d in target.dependencies
                ]
                print(f"    links: {', '.join(deps)}")

    def resolve(self, strict: bool = False) -> None:
        """Resolve all targets in two phases.

        Phase 1: Resolve build targets (compiles, links)
            This populates object_nodes and output_nodes for libraries/programs.

        Phase 2: Resolve pending sources (Install, InstallAs, etc.)
            This handles targets that reference outputs from other targets.
            Because Phase 1 has run, output_nodes are now populated.

        After resolution, each target's nodes are fully populated and ready
        for generation. Validation is run automatically and warnings logged.

        Args:
            strict: If True, raise an exception on validation errors.
                   If False (default), log warnings but continue.
        """
        from pcons.core.resolver import Resolver

        resolver = Resolver(self)

        # Phase 1: Resolve build targets
        resolver.resolve()

        # Phase 2: Resolve pending sources (Install, etc.)
        resolver.resolve_pending_sources()

        # Validate and report issues
        errors = self.validate()
        if errors:
            for error in errors:
                logger.warning("Validation: %s", error)
            if strict:
                from pcons.core.errors import PconsError

                raise PconsError(
                    f"Validation failed with {len(errors)} error(s). "
                    f"First error: {errors[0]}"
                )

        # Check for graph output requests (set by CLI --graph/--mermaid options)
        self._output_graphs_if_requested()

    def _output_graphs_if_requested(self) -> None:
        """Output dependency graphs if requested via PCONS_GRAPH/PCONS_MERMAID env vars."""
        import os

        # DOT format graph
        graph_path = os.environ.get("PCONS_GRAPH")
        if graph_path:
            dot_content = self.dump_graph()
            if graph_path == "-":
                print("# DOT dependency graph")
                print(dot_content)
            else:
                Path(graph_path).write_text(dot_content)
                logger.info("Wrote DOT graph to %s", graph_path)

        # Mermaid format graph
        mermaid_path = os.environ.get("PCONS_MERMAID")
        if mermaid_path:
            from pcons.generators.mermaid import MermaidGenerator

            if mermaid_path == "-":
                # Write to stdout
                print("# Mermaid dependency graph")
                gen = MermaidGenerator(output_filename="deps.mmd")
                # Generate to a temp file, then print
                import tempfile

                with tempfile.TemporaryDirectory() as tmpdir:
                    gen.generate(self, Path(tmpdir))
                    mermaid_content = (Path(tmpdir) / "deps.mmd").read_text()
                    print(mermaid_content)
            else:
                # Write to file
                output_path = Path(mermaid_path)
                gen = MermaidGenerator(output_filename=output_path.name)
                gen.generate(self, output_path.parent)
                logger.info("Wrote Mermaid graph to %s", mermaid_path)

    # Target factory methods for the target-centric build model

    def StaticLibrary(
        self,
        name: str,
        env: Env,
        sources: list[str | Path | Node] | None = None,
    ) -> Target:
        """Create a static library target.

        Args:
            name: Target name (e.g., "mylib").
            env: Environment to use for building.
            sources: Source files for the library.

        Returns:
            A new Target configured as a static library.
        """
        target = Target(
            name,
            target_type=TargetType.STATIC_LIBRARY,
            defined_at=get_caller_location(),
        )
        target._env = env
        target._project = self
        if sources:
            source_nodes = self._normalize_sources(sources)
            target.add_sources(source_nodes)
        self.add_target(target)
        return target

    def SharedLibrary(
        self,
        name: str,
        env: Env,
        sources: list[str | Path | Node] | None = None,
    ) -> Target:
        """Create a shared library target.

        Args:
            name: Target name (e.g., "mylib").
            env: Environment to use for building.
            sources: Source files for the library.

        Returns:
            A new Target configured as a shared library.
        """
        target = Target(
            name,
            target_type=TargetType.SHARED_LIBRARY,
            defined_at=get_caller_location(),
        )
        target._env = env
        target._project = self
        if sources:
            source_nodes = self._normalize_sources(sources)
            target.add_sources(source_nodes)
        self.add_target(target)
        return target

    def Program(
        self,
        name: str,
        env: Env,
        sources: list[str | Path | Node] | None = None,
    ) -> Target:
        """Create a program (executable) target.

        Args:
            name: Target name (e.g., "myapp").
            env: Environment to use for building.
            sources: Source files for the program.

        Returns:
            A new Target configured as a program.
        """
        target = Target(
            name, target_type=TargetType.PROGRAM, defined_at=get_caller_location()
        )
        target._env = env
        target._project = self
        if sources:
            source_nodes = self._normalize_sources(sources)
            target.add_sources(source_nodes)
        self.add_target(target)
        return target

    def HeaderOnlyLibrary(
        self,
        name: str,
        include_dirs: list[str | Path] | None = None,
    ) -> Target:
        """Create a header-only (interface) library target.

        Header-only libraries have no sources to compile but can provide
        usage requirements (include directories, defines, etc.) to
        targets that link against them.

        Args:
            name: Target name (e.g., "my_headers").
            include_dirs: Include directories to propagate to dependents.

        Returns:
            A new Target configured as an interface library.
        """
        target = Target(
            name, target_type=TargetType.INTERFACE, defined_at=get_caller_location()
        )
        if include_dirs:
            for inc_dir in include_dirs:
                target.public.include_dirs.append(Path(inc_dir))
        self.add_target(target)
        return target

    def ObjectLibrary(
        self,
        name: str,
        env: Env,
        sources: list[str | Path | Node] | None = None,
    ) -> Target:
        """Create an object library target (compiles but doesn't link).

        Object libraries compile their sources to object files but don't
        produce a final library or executable. Useful for compiling sources
        that will be used by multiple targets.

        Args:
            name: Target name.
            env: Environment to use for building.
            sources: Source files to compile.

        Returns:
            A new Target configured as an object library.
        """
        target = Target(
            name, target_type=TargetType.OBJECT, defined_at=get_caller_location()
        )
        target._env = env
        target._project = self
        if sources:
            source_nodes = self._normalize_sources(sources)
            target.add_sources(source_nodes)
        self.add_target(target)
        return target

    def _normalize_sources(
        self,
        sources: list[str | Path | Node],
    ) -> list[Node]:
        """Convert source paths/strings to nodes.

        Uses project's node() for deduplication.

        Args:
            sources: List of source files (strings, Paths, or Nodes).

        Returns:
            List of Node objects.
        """
        result: list[Node] = []
        for src in sources:
            if isinstance(src, Node):
                result.append(src)
            else:
                result.append(self.node(src))
        return result

    def Install(
        self,
        dest_dir: Path | str,
        sources: list[Target | Node | Path | str],
        *,
        name: str | None = None,
    ) -> Target:
        """Install files to a destination directory.

        Creates copy operations for each source file to the destination
        directory. The returned target depends on all the installed files.

        Note: Sources are resolved lazily during project.resolve(), so this
        can be called before or after defining the source targets.

        Args:
            dest_dir: Destination directory path.
            sources: Files to install. Can be:
                - Target: Installs the target's output files
                - Node: Installs the node
                - Path/str: Installs the file at that path
            name: Optional name for the install target (default: "install_<dirname>")

        Returns:
            A Target representing the install operation.

        Example:
            # Install a library and headers
            project.Install(
                "dist/lib",
                [mylib],  # Installs libmylib.a (or .so)
            )
            project.Install(
                "dist/include",
                project.Glob("include/*.h"),
            )

            # Bundle creation
            bundle_dir = Path("build/MyPlugin.bundle/Contents/MacOS")
            project.Install(bundle_dir, [plugin_lib])
        """
        dest_dir = Path(dest_dir)
        target_name = name or f"install_{dest_dir.name}"

        # Handle duplicate target names by appending a suffix
        base_name = target_name
        counter = 1
        while target_name in self._targets:
            target_name = f"{base_name}_{counter}"
            counter += 1
        if target_name != base_name:
            logger.warning(
                "Install target renamed from '%s' to '%s' to avoid conflict",
                base_name,
                target_name,
            )

        # Create the install target with pending sources
        # Sources will be resolved during project.resolve()
        install_target = Target(
            target_name,
            target_type=TargetType.INTERFACE,
            defined_at=get_caller_location(),
        )

        # Store for lazy resolution
        install_target._pending_sources = list(sources)
        install_target._install_dest_dir = dest_dir

        self.add_target(install_target)
        return install_target

    def InstallAs(
        self,
        dest: Path | str,
        source: Target | Node | Path | str,
        *,
        name: str | None = None,
    ) -> Target:
        """Install a file to a specific destination path.

        Unlike Install(), this copies a single file to an exact path,
        allowing rename during installation.

        Note: Source is resolved lazily during project.resolve(), so this
        can be called before or after defining the source target.

        Args:
            dest: Full destination path (including filename).
            source: Source file (Target, Node, Path, or string).
            name: Optional name for the install target.

        Returns:
            A Target representing the install operation.

        Example:
            project.InstallAs(
                bundle_dir / "markymark.ofx",
                ofx_plugin,
            )
        """
        dest = Path(dest)
        target_name = name or f"install_{dest.name}"

        # Handle duplicate target names by appending a suffix
        base_name = target_name
        counter = 1
        while target_name in self._targets:
            target_name = f"{base_name}_{counter}"
            counter += 1
        if target_name != base_name:
            logger.warning(
                "Install target renamed from '%s' to '%s' to avoid conflict",
                base_name,
                target_name,
            )

        # Create the install target with pending source
        # Source will be resolved during project.resolve()
        install_target = Target(
            target_name,
            target_type=TargetType.INTERFACE,
            defined_at=get_caller_location(),
        )

        # Store for lazy resolution
        install_target._pending_sources = [source]
        install_target._install_as_dest = dest

        self.add_target(install_target)
        return install_target

    def _name_from_output(self, output: str | Path, suffixes: list[str]) -> str:
        """Derive target name from output path by stripping archive suffixes.

        Args:
            output: Output path (e.g., "dist/docs.tar.gz").
            suffixes: List of suffixes to strip (e.g., [".tar.gz", ".tar"]).

        Returns:
            Derived name (e.g., "dist/docs").
        """
        name = str(output)
        for suffix in suffixes:
            if name.endswith(suffix):
                name = name[: -len(suffix)]
                break
        return name

    def Tarfile(
        self,
        env: Env,
        *,
        output: str | Path,
        sources: list[str | Path | Node | Target] | None = None,
        compression: str | None = None,
        base_dir: str | Path | None = None,
        name: str | None = None,
    ) -> Target:
        """Create a tar archive from source files/directories.

        Args:
            env: Environment for this build.
            output: Output archive path (.tar, .tar.gz, .tar.bz2, .tar.xz).
            sources: Input files, directories, and/or Targets.
            compression: Compression type (None, "gzip", "bz2", "xz").
                        If None, inferred from output extension.
            base_dir: Base directory for archive paths (default: ".").
            name: Optional target name for `ninja <name>`. Derived from output if not specified.

        Returns:
            Target representing the archive.

        Example:
            docs = project.Tarfile(env,
                output="dist/docs.tar.gz",
                sources=["docs/", "README.md"])
            project.Install("packages/", [docs])  # Works because it's a Target
        """
        output_path = Path(output)

        # Infer compression from extension if not specified
        if compression is None:
            if str(output).endswith(".tar.gz") or str(output).endswith(".tgz"):
                compression = "gzip"
            elif str(output).endswith(".tar.bz2"):
                compression = "bz2"
            elif str(output).endswith(".tar.xz"):
                compression = "xz"
            # .tar gets no compression

        # Derive name from output if not specified
        if name is None:
            name = self._name_from_output(
                output, [".tar.gz", ".tar.bz2", ".tar.xz", ".tgz", ".tar"]
            )

        target = Target(
            name,
            target_type=TargetType.ARCHIVE,
            defined_at=get_caller_location(),
        )
        target._env = env
        target._project = self

        # Store sources for lazy resolution
        target._pending_sources = list(sources) if sources else []

        # Store build info for Ninja generator
        target._build_info = {
            "tool": "tarfile",
            "output": str(output_path),
            "compression": compression,
            "base_dir": str(base_dir) if base_dir else ".",
        }

        self.add_target(target)
        return target

    def Zipfile(
        self,
        env: Env,
        *,
        output: str | Path,
        sources: list[str | Path | Node | Target] | None = None,
        base_dir: str | Path | None = None,
        name: str | None = None,
    ) -> Target:
        """Create a zip archive from source files/directories.

        Args:
            env: Environment for this build.
            output: Output archive path (.zip).
            sources: Input files, directories, and/or Targets.
            base_dir: Base directory for archive paths (default: ".").
            name: Optional target name for `ninja <name>`. Derived from output if not specified.

        Returns:
            Target representing the archive.

        Example:
            release = project.Zipfile(env,
                output="dist/release.zip",
                sources=["bin/", "lib/", "README.md"])
        """
        output_path = Path(output)

        # Derive name from output if not specified
        if name is None:
            name = self._name_from_output(output, [".zip"])

        target = Target(
            name,
            target_type=TargetType.ARCHIVE,
            defined_at=get_caller_location(),
        )
        target._env = env
        target._project = self

        # Store sources for lazy resolution
        target._pending_sources = list(sources) if sources else []

        # Store build info for Ninja generator
        target._build_info = {
            "tool": "zipfile",
            "output": str(output_path),
            "base_dir": str(base_dir) if base_dir else ".",
        }

        self.add_target(target)
        return target

    def __repr__(self) -> str:
        return (
            f"Project({self.name!r}, "
            f"targets={len(self._targets)}, "
            f"envs={len(self._environments)})"
        )
