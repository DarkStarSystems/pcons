# SPDX-License-Identifier: MIT
"""Project container for pcons builds.

The Project is the top-level container that holds all environments,
targets, and nodes for a build. It provides node deduplication and
serves as the context for build descriptions.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, overload

from pcons.core.builder_registry import BuilderRegistry
from pcons.core.environment import Environment as Env
from pcons.core.graph import (
    collect_all_nodes,
    detect_cycles_in_targets,
    topological_sort_targets,
)
from pcons.core.node import AliasNode, DirNode, FileNode, Node
from pcons.core.paths import PathResolver
from pcons.core.target import Target, split_qualified_name
from pcons.util.source_location import SourceLocation, get_caller_location

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from pcons.core._project_builder_stubs import _ProjectBuilders
    from pcons.tools.toolchain import Toolchain
else:
    # At runtime, builder lookup goes through Project.__getattr__; the
    # mixin's only purpose is to declare typed methods for static analysis.
    _ProjectBuilders = object


class Project(_ProjectBuilders):
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
        "_resolved",
        "_path_resolver",
        "_found_packages",
        "_package_finder_chain",
        "defined_at",
        "_parent",
        "_children",
        "_subdir",
    )

    __current: Project | None = None
    __top_level: Project | None = None

    @staticmethod
    def _clear_tree() -> None:
        """Clear the project tree (for testing purposes)."""
        Project.__current = None
        Project.__top_level = None

    def __init__(
        self,
        name: str,
        *,
        root_dir: Path | str | None = None,
        build_dir: Path | str | None = None,
        config: Any = None,
        defined_at: SourceLocation | None = None,
    ) -> None:
        """Create a project.

        Args:
            name: Project name.
            root_dir: Project root directory. Defaults to the
                PCONS_SOURCE_DIR environment variable if set (the CLI
                sets this automatically), then the directory containing
                the calling script, then the current working directory.
            build_dir: Directory for build outputs. Defaults to the
                PCONS_BUILD_DIR environment variable if set (the CLI
                sets this automatically), otherwise "build".
            config: Cached configuration from configure phase.
            defined_at: Source location where project was created.
        """
        self.name = name
        if root_dir is None and Project.__top_level is None:
            root_dir = os.environ.get("PCONS_SOURCE_DIR")
        if root_dir is None:
            # Infer from the script that called Project()
            caller = defined_at or get_caller_location()
            caller_file = Path(caller.filename)
            if caller_file.exists():
                root_dir = str(caller_file.parent)
        self.root_dir = Path(root_dir) if root_dir else Path.cwd()
        if build_dir is None:
            build_dir = os.environ.get("PCONS_BUILD_DIR", "build")
        if not self.root_dir.is_absolute():
            raise ValueError(f"Root directory must be absolute (got: {self.root_dir})")
        bd = Path(build_dir)
        if bd.is_absolute():
            try:
                bd = bd.relative_to(self.root_dir)
            except ValueError:
                pass  # Out-of-tree build — keep absolute
        self.build_dir = bd
        self._environments: list[Env] = []
        self._targets: list[Target] = []
        self._nodes: dict[Path, Node] = {}
        self._aliases: dict[str, AliasNode] = {}
        self._default_targets: list[Target] = []
        self._config = config
        self._resolved = False
        self._path_resolver = PathResolver(self.root_dir, self.build_dir)
        self._found_packages: dict[tuple[str, str | None, tuple[str, ...]], Target] = {}
        self._package_finder_chain: Any = None  # Lazy-initialized FinderChain
        self.defined_at = defined_at or get_caller_location()
        self._subdir = None
        self._children: list[Project] = []

        # Auto-register with global registry (for CLI access)
        from pcons import _register_project

        _register_project(self)

        if Project.__current is not None:
            self._parent = Project.__current
        else:
            self._parent = None

        if self._parent:
            self._parent._children.append(self)
            self.build_dir = self._parent.build_dir
            # If the child project's root_dir is inside the top-level project,
            # normalize it so that the child's `root_dir` becomes the top-level
            # root and `_subdir` holds the relative path under the top-level.
            top_root = Project.top_level().root_dir
            rel = self.root_dir.relative_to(top_root)
            self.root_dir = top_root
            self._subdir = str(rel)
            # Recreate the path resolver now that root_dir/build_dir changed
            self._path_resolver = PathResolver(self.root_dir, self.build_dir)

        Project.__current = self
        if Project.__top_level is None:
            Project.__top_level = self

    @staticmethod
    def current() -> Project:
        if Project.__current is None:
            raise ValueError("no project is currently active")
        return Project.__current

    @staticmethod
    def top_level() -> Project:
        if Project.__top_level is None:
            raise ValueError("no top-level project is currently active")
        return Project.__top_level

    @property
    def is_top_level(self) -> bool:
        return self == Project.top_level()

    @property
    def parent(self) -> Project:
        """Get the parent project if this is a subdir, or None if top-level."""
        if self._parent is None:
            raise ValueError("This project has no parent (it is top-level).")
        return self._parent

    @property
    def current_dir(self) -> Path:
        """Get the current directory for this project, taking subdirs into account."""
        if self._subdir:
            return self.root_dir / self._subdir
        return self.root_dir

    @contextmanager
    def _enter_subdir(self, subdir: str | Path) -> Generator[None, None, None]:
        """Context manager for entering a subdirectory in the project."""
        old_subdir = self._subdir
        self._subdir = subdir if old_subdir is None else f"{old_subdir}/{subdir}"
        try:
            yield
        finally:
            self._subdir = old_subdir
            Project.__current = self

    @property
    def config(self) -> Any:
        """Get the cached configuration."""
        return self._config

    @config.setter
    def config(self, value: Any) -> None:
        """Set the cached configuration."""
        self._config = value

    @property
    def path_resolver(self) -> PathResolver:
        """Get the path resolver for this project."""
        if self._subdir:
            return self._path_resolver.subdir(self._subdir)
        else:
            return self._path_resolver

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

    def _canonicalize_path(self, path: Path) -> Path:
        """Convert path to canonical form for node storage.

        Canonical: relative to project root if under it, absolute otherwise.
        Uses pure path arithmetic (no filesystem access).
        """
        if path.is_absolute():
            try:
                return path.relative_to(self.current_dir)
            except ValueError:
                return path  # External path
        return Path(os.path.normpath(path))

    def node(self, path: Path | str) -> FileNode:
        """Get or create a file node for a path.

        This provides node deduplication - the same path always
        returns the same node instance.

        Args:
            path: Path to the file.

        Returns:
            FileNode for the path.
        """
        path = self._canonicalize_path(Path(path))
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
        path = self._canonicalize_path(Path(path))
        if path not in self._nodes:
            self._nodes[path] = DirNode(path, defined_at=get_caller_location())
        node = self._nodes[path]
        if not isinstance(node, DirNode):
            raise TypeError(
                f"Path {path} is registered as {type(node).__name__}, not DirNode"
            )
        return node

    def _add_target(self, target: Target) -> None:
        """Register a target with the project (should not be called directly).

        Only Target should call this method, via Target.__init__,
        to ensure that targets are registered as soon as they are created.

        Args:
            target: Target to register.

        Raises:
            ValueError: If a target with the same name already exists.
        """
        if (
            existing := self.get_target(
                target.name, raise_if_missing=False, recursive=False
            )
        ) is not None:
            raise ValueError(
                f"Target '{target.name}' already exists "
                f"(defined at {existing.defined_at})"
            )
        self._targets.append(target)

    @overload
    def get_target(
        self, name: str, raise_if_missing: Literal[True] = ..., recursive: bool = ...
    ) -> Target: ...

    @overload
    def get_target(
        self, name: str, raise_if_missing: Literal[False], recursive: bool = ...
    ) -> Target | None: ...

    def get_target(
        self, name: str, raise_if_missing: bool = True, recursive: bool = True
    ) -> Target | None:
        """Get a target by name.

        Args:
            name: Target name.
            raise_if_missing: Whether to raise an exception if the target is not found.

        Returns:
            The target, or None if not found and raise_if_missing is False.

        Raises:
            KeyError: If the target is not found and raise_if_missing is True.
        """

        project, target_name = split_qualified_name(name)
        if project is not None:
            if project == self.name:
                for target in self._targets:
                    if target.name == target_name:
                        return target
                else:
                    if raise_if_missing:
                        raise KeyError(f"Target '{name}' not found")
                    return None
        else:
            for target in self._targets:
                if target.name == target_name:
                    return target

        if recursive:
            targets_found = []
            for child in self._children:
                if (
                    target := child.get_target(name, raise_if_missing=False)
                ) is not None:
                    targets_found.append(target)
            if len(targets_found) > 1:
                raise KeyError(
                    f"Multiple targets named '{name}' found in child projects: "
                    f"{', '.join(t.qualified_name for t in targets_found)}\n"
                    f"Use qualified names (e.g., '{targets_found[0].qualified_name}') to disambiguate."
                )
            if targets_found:
                return targets_found[0]

        if raise_if_missing:
            raise KeyError(f"Target '{name}' not found")
        return None

    def get_targets(self, *names: str) -> list[Target]:
        """Get multiple targets by name.

        Args:
            *names: Target names.

        Returns:
            List of targets.

        Raises:
            KeyError: If any target is not found.
        """
        return [self.get_target(name) for name in names]

    @property
    def targets(self) -> list[Target]:
        """Get all registered targets."""
        results: list[Target] = list(self._targets)
        for child in self._children:
            results.extend(child.targets)
        return results

    @property
    def environments(self) -> list[Env]:
        """Get all registered environments."""
        return list(self._environments)

    @property
    def default_environment(self) -> Env:
        """Get the default environment (first one registered).

        Raises:
            ValueError: If no environments have been registered.
        """
        if not self._environments:
            raise ValueError("No environments have been registered in this project.")
        return self._environments[0]

    def Alias(
        self, name: str, *targets: Target | Node | list[Target | Node]
    ) -> AliasNode:
        """Create a named alias for targets.

        Aliases can be used as build targets (e.g., 'ninja test').

        Args:
            name: Alias name.
            *targets: Targets, nodes, or lists of them.

        Returns:
            AliasNode for this alias.
        """
        if name not in self._aliases:
            self._aliases[name] = AliasNode(name, defined_at=get_caller_location())

        alias = self._aliases[name]
        # Flatten lists so Alias("name", [a, b]) works like Alias("name", a, b)
        flat: list[Target | Node] = []
        for t in targets:
            if isinstance(t, list):
                flat.extend(t)
            else:
                flat.append(t)
        for t in flat:
            match t:
                case Target():
                    # Defer resolution: output_nodes may not be populated until resolve()
                    alias.add_deferred_target(t)
                case Node():
                    alias.add_target(t)
                case _:
                    raise TypeError(
                        f"Alias targets must be Target, Node, or list of them, got {type(t)}"
                    )

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
                target = self.get_target(t)
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
        return collect_all_nodes(self._targets)

    def _to_build_relative(self, p: Path) -> Path:
        """Strip the build_dir prefix from a canonicalized path.

        Used by get_child_nodes/has_child_nodes to normalize paths for
        comparison regardless of whether they include the build_dir prefix.
        """
        parts = p.parts
        bd_parts = self.build_dir.parts
        n = len(bd_parts)
        if bd_parts and parts[:n] == bd_parts:
            return Path(*parts[n:]) if len(parts) > n else Path(".")
        return p

    def get_child_nodes(self, path: Path | str) -> list[FileNode]:
        """Get all project nodes whose path is a descendant of the given path.

        Uses the same canonicalization as the node registry -- no filesystem
        access.  Both the query path and registered node paths are
        normalized to build-dir-relative form before comparison so that
        paths supplied with and without the ``build_dir`` prefix match.

        Args:
            path: Directory path to search under.

        Returns:
            List of FileNodes whose canonical path is strictly under *path*.
        """
        canonicalize = self._path_resolver.canonicalize
        check_path = self._to_build_relative(canonicalize(Path(path)))
        children: list[FileNode] = []
        for node_path, node in self._nodes.items():
            if not isinstance(node, FileNode):
                continue
            canonical = self._to_build_relative(canonicalize(node_path))
            if canonical == check_path:
                continue
            try:
                canonical.relative_to(check_path)
                children.append(node)
            except ValueError:
                continue
        return children

    def has_child_nodes(self, path: Path | str) -> bool:
        """Check whether any registered node is a descendant of *path*.

        Equivalent to ``bool(self.get_child_nodes(path))`` but short-circuits
        on the first match for efficiency.
        """
        canonicalize = self._path_resolver.canonicalize
        check_path = self._to_build_relative(canonicalize(Path(path)))
        for node_path in self._nodes:
            canonical = self._to_build_relative(canonicalize(node_path))
            if canonical != check_path:
                try:
                    canonical.relative_to(check_path)
                    return True
                except ValueError:
                    continue
        return False

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
        cycles = detect_cycles_in_targets(self._targets)
        for cycle in cycles:
            from pcons.core.errors import DependencyCycleError

            errors.append(DependencyCycleError(cycle))

        # Check for missing sources
        from pcons.core.errors import MissingSourceError

        for target in self._targets:
            for source in target.sources:
                if isinstance(source, FileNode):
                    # Only check source files (not generated files)

                    if source.builder is None:
                        p = source.path
                        if not p.is_absolute():
                            p = Project.top_level().root_dir / p
                        if not p.exists():
                            errors.append(
                                MissingSourceError(str(p), target_name=target.name)
                            )

        return errors

    def build_order(self) -> list[Target]:
        """Get targets in the order they should be built.

        Returns:
            Targets sorted so dependencies come before dependents.
        """
        return topological_sort_targets(self._targets)

    def print_targets(self) -> None:
        """Print a human-readable summary of all targets.

        Useful for debugging. Shows target names, types, and dependencies.
        """
        print(f"Project: {self.name}")
        print(f"Build dir: {self.build_dir}")
        print(f"Targets ({len(self._targets)}):")

        for target in sorted(self._targets):
            print(f"  {target.name} ({target.target_type})")
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
            This populates intermediate_nodes and output_nodes for libraries/programs.

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

        self._resolved = True

        # Check for graph output requests (set by CLI --graph/--mermaid options)
        self._output_graphs_if_requested()

    def _output_graphs_if_requested(self) -> None:
        """Output dependency graphs if requested via PCONS_GRAPH/PCONS_MERMAID env vars."""
        graph_path = os.environ.get("PCONS_GRAPH")
        if graph_path:
            from pcons.generators.dot import DotGenerator

            self._output_graph(DotGenerator, graph_path, "deps.dot", "DOT")

        mermaid_path = os.environ.get("PCONS_MERMAID")
        if mermaid_path:
            from pcons.generators.mermaid import MermaidGenerator

            self._output_graph(MermaidGenerator, mermaid_path, "deps.mmd", "Mermaid")

    def _output_graph(
        self,
        generator_class: type,
        output_path_str: str,
        default_filename: str,
        format_name: str,
    ) -> None:
        """Write a dependency graph to stdout or a file.

        Args:
            generator_class: The generator class to instantiate.
            output_path_str: "-" for stdout, or a file path.
            default_filename: Filename to use when writing to stdout via temp dir.
            format_name: Human-readable format name for log messages.
        """
        import tempfile

        if output_path_str == "-":
            print(f"# {format_name} dependency graph")
            with tempfile.TemporaryDirectory() as tmpdir:
                gen = generator_class(
                    output_filename=default_filename, output_dir=Path(tmpdir)
                )
                gen.generate(self)
                print((Path(tmpdir) / default_filename).read_text())
        else:
            output_path = Path(output_path_str)
            gen = generator_class(
                output_filename=output_path.name, output_dir=output_path.parent
            )
            gen.generate(self)
            logger.info("Wrote %s graph to %s", format_name, output_path_str)

    def generate(self) -> None:
        """Generate build files (convenience method).

        Selects the appropriate generator (Ninja by default, overridable
        via ``--generator`` CLI flag or ``PCONS_GENERATOR`` env var),
        auto-resolves the project if needed, and writes the build files.

        For advanced usage (e.g., disabling compile_commands.json),
        use ``Generator().generate(project)`` directly.
        """
        from pcons import Generator

        Generator().generate(self)

    def generate_pc_file(
        self,
        target: Target,
        *,
        version: str = "0.0.0",
        description: str = "",
        install_prefix: str = "/usr/local",
    ) -> Path:
        """Generate a pkg-config .pc file for a library target.

        Writes a standard .pc file based on the target's public usage
        requirements (include_dirs, link_libs, defines, link_flags).
        The file is written to the build directory and can be installed
        with ``project.Install("lib/pkgconfig", [pc_path])``.

        This runs at configure time (like ``configure_file()``), not as
        a ninja build step. Uses write-if-changed to avoid unnecessary
        downstream rebuilds.

        Args:
            target: The library target to generate a .pc file for.
            version: Package version string (e.g., "1.2.0").
            description: One-line package description.
            install_prefix: Expected install prefix. The .pc file uses
                ``${prefix}`` variables so it's relocatable.

        Returns:
            Path to the generated .pc file.

        Example:
            lib = project.StaticLibrary("foo", env, sources=["src/foo.c"])
            lib.public.include_dirs.append("include")

            pc = project.generate_pc_file(lib, version="1.2.0")
            project.Install("lib/pkgconfig", [pc])
        """
        from pcons.packages.imported import ImportedTarget

        name = target.name

        # Build Requires: list from dependencies that came from pkg-config
        requires: list[str] = []
        for dep in target.dependencies:
            if isinstance(dep, ImportedTarget) and dep.package is not None:
                if getattr(dep.package, "found_by", None) == "pkg-config":
                    requires.append(dep.name)

        # Build Cflags: from public include_dirs and defines
        # Rewrite include dirs to use ${includedir} for relocatability:
        # - dirs under root_dir → ${includedir} (installed layout)
        # - relative dirs like "include" → ${includedir}
        # - absolute dirs outside the project → kept as-is
        cflags_parts: list[str] = []
        seen_includedir = False
        for inc_dir in target.public.include_dirs:
            inc_path = Path(inc_dir)
            # Check the original string for Unix-style absolute paths
            # (starting with /) since Path("/opt/...") is not absolute on Windows.
            inc_str = str(inc_dir)
            is_abs = inc_path.is_absolute() or inc_str.startswith("/")
            if not is_abs:
                # Relative path (e.g., "include") — use ${includedir}
                if not seen_includedir:
                    cflags_parts.append("-I${includedir}")
                    seen_includedir = True
            else:
                # Absolute path — check if it's under the project root
                try:
                    inc_path.relative_to(self.root_dir)
                    # Under project root — will be installed to ${includedir}
                    if not seen_includedir:
                        cflags_parts.append("-I${includedir}")
                        seen_includedir = True
                except ValueError:
                    # External path (e.g., /usr/include) — keep as-is
                    cflags_parts.append(f"-I{inc_dir}")
        for define in target.public.defines:
            cflags_parts.append(f"-D{define}")
        for flag in target.public.compile_flags:
            cflags_parts.append(str(flag))

        # Build Libs: the library itself plus any public link flags/libs
        # Exclude libs that are covered by Requires
        libs_parts: list[str] = ["-L${libdir}", f"-l{name}"]
        for flag in target.public.link_flags:
            libs_parts.append(str(flag))
        for lib in target.public.link_libs:
            if lib not in requires:
                libs_parts.append(f"-l{lib}")

        # Write .pc content
        lines = [
            f"prefix={install_prefix}",
            "libdir=${prefix}/lib",
            "includedir=${prefix}/include",
            "",
            f"Name: {name}",
            f"Description: {description or name}",
            f"Version: {version}",
        ]
        if requires:
            lines.append(f"Requires: {' '.join(requires)}")
        lines.append(f"Libs: {' '.join(libs_parts)}")
        if cflags_parts:
            lines.append(f"Cflags: {' '.join(cflags_parts)}")

        content = "\n".join(lines) + "\n"

        # Write-if-changed
        pc_path = self.build_dir / f"{name}.pc"
        pc_path.parent.mkdir(parents=True, exist_ok=True)
        if pc_path.exists() and pc_path.read_text() == content:
            return pc_path
        pc_path.write_text(content)
        logger.info("Generated %s", pc_path)
        return pc_path

    # =========================================================================
    # Package Discovery
    # =========================================================================

    def find_package(
        self,
        name: str,
        *,
        version: str | None = None,
        components: list[str] | None = None,
        required: bool = True,
    ) -> Target | None:
        """Find an external package and return it as an ImportedTarget.

        Searches for the package using the configured finder chain
        (default: PkgConfigFinder → SystemFinder). Results are cached
        so repeated calls with the same arguments return the same target.

        The returned target can be used as a dependency via target.link()
        or applied directly to an environment via env.use().

        Args:
            name: Package name (e.g., "zlib", "openssl").
            version: Optional version requirement (e.g., ">=3.0").
            components: Optional list of package components.
            required: If True (default), raises PackageNotFoundError when
                     the package is not found. If False, returns None.

        Returns:
            An ImportedTarget representing the package, or None if not
            found and required=False.

        Raises:
            PackageNotFoundError: If the package is not found and required=True.

        Example:
            zlib = project.find_package("zlib")
            openssl = project.find_package("openssl", version=">=3.0")
            boost = project.find_package("boost", components=["filesystem"])

            app.link(zlib)
            env.use(openssl)
        """
        cache_key = (name, version, tuple(components or []))
        if cache_key in self._found_packages:
            return self._found_packages[cache_key]

        if self._package_finder_chain is None:
            from pcons.packages.finders import (
                FinderChain,
                PkgConfigFinder,
                SystemFinder,
            )

            self._package_finder_chain = FinderChain(
                [PkgConfigFinder(), SystemFinder()]
            )

        pkg = self._package_finder_chain.find(name, version, components)
        if pkg is None:
            if required:
                from pcons.core.errors import PackageNotFoundError

                raise PackageNotFoundError(name, version)
            return None

        from pcons.packages.imported import ImportedTarget

        target = ImportedTarget.from_package(pkg, components=components)
        self._found_packages[cache_key] = target
        return target

    def add_package_finder(self, finder: Any) -> None:
        """Add a package finder to the front of the search chain.

        Custom finders are tried before the default finders (PkgConfig,
        System). Use this to add Conan, vcpkg, or custom finders.

        Args:
            finder: A BaseFinder instance.

        Example:
            from pcons.packages.finders import ConanFinder

            project.add_package_finder(ConanFinder(config, conanfile="conanfile.txt"))
            zlib = project.find_package("zlib")  # Tries Conan first
        """
        if self._package_finder_chain is None:
            from pcons.packages.finders import (
                FinderChain,
                PkgConfigFinder,
                SystemFinder,
            )

            self._package_finder_chain = FinderChain(
                [finder, PkgConfigFinder(), SystemFinder()]
            )
        else:
            self._package_finder_chain._finders.insert(0, finder)

    # Command is kept as a wrapper since it delegates to env.Command()
    # and doesn't fit the registry pattern well

    def Command(
        self,
        name: str,
        env: Env,
        *,
        target: str | Path | list[str | Path],
        source: str | Path | list[str | Path] | None = None,
        command: str | list[str] = "",
        restat: bool = False,
    ) -> Target:
        """Create a custom command target.

        This is a convenience wrapper around env.Command() that follows
        the target-centric API pattern (project.Program, project.StaticLibrary, etc.).

        Args:
            name: Target name for `ninja <name>`.
            env: Environment to use (for variable substitution).
            target: Output file(s) that the command produces.
            source: Input file(s) that the command depends on.
            command: The shell command to run. Supports variable substitution:
                    - $SOURCE / $in: First source file
                    - $SOURCES: All source files (space-separated)
                    - $TARGET / $out: First target file
                    - $TARGETS: All target files (space-separated)
            restat: If True, Ninja will re-check the output timestamp after
                   running the command. If the output didn't actually change,
                   downstream targets won't be rebuilt.

        Returns:
            A new Target configured as a command.

        Example:
            gen_header = project.Command(
                "gen-header",
                env,
                target=build_dir / "generated.h",
                source=src_dir / "spec.yml",
                command="python gen.py $SOURCE -o $TARGET",
            )
        """
        return env.Command(
            target=target, source=source, command=command, name=name, restat=restat
        )

    def __str__(self) -> str:
        """User-friendly string representation for debugging."""
        lines = [f"Project: {self.name}"]
        lines.append(f"  Root: {self.root_dir}")
        lines.append(f"  Build: {self.build_dir}")
        lines.append(f"  Targets: {len(self._targets)}")
        for target in self._targets[:5]:
            target_type = target.target_type or "unknown"
            lines.append(f"    - {target.name} ({target_type})")
        if len(self._targets) > 5:
            lines.append(f"    ... and {len(self._targets) - 5} more")
        lines.append(f"  Environments: {len(self._environments)}")
        return "\n".join(lines)

    def __repr__(self) -> str:
        return (
            f"Project({self.name!r}, "
            f"targets={len(self._targets)}, "
            f"envs={len(self._environments)})"
        )

    if not TYPE_CHECKING:
        # __getattr__ is hidden from type checkers so that unknown attribute
        # access on a Project is rejected (and typed builder methods from
        # _ProjectBuilders take effect). At runtime it dispatches registered
        # builders via the BuilderRegistry. User-registered @builder targets
        # are not in _ProjectBuilders, so calls to them appear as unresolved
        # attributes to type checkers and require a `type: ignore` /
        # `ty: ignore` at the call site (see examples/15_custom_builder).
        def __getattr__(self, name: str) -> Any:
            """Dynamic attribute access for registered builders.

            Allows registered builders to be called as methods on Project
            instances, e.g. `project.InstallSymlink(...)` for a custom
            `@builder` named "InstallSymlink".

            Raises:
                AttributeError: If the attribute is not a registered builder.
            """
            registration = BuilderRegistry.get(name)
            if registration is not None:
                if registration.platforms:
                    import sys

                    if sys.platform not in registration.platforms:
                        raise AttributeError(
                            f"Builder '{name}' is only available on "
                            f"{', '.join(registration.platforms)} "
                            f"(current platform: {sys.platform})"
                        )
                return self._make_builder_method(registration)

            raise AttributeError(
                f"'{type(self).__name__}' object has no attribute '{name}'"
            )

    def __dir__(self) -> list[str]:
        """Include registered builder names in dir() output.

        This enables IDE auto-completion for dynamically available builders.
        """
        # Get the default attributes
        attrs = list(super().__dir__())
        # Add registered builder names
        attrs.extend(BuilderRegistry.names())
        return attrs

    def _make_builder_method(self, registration: Any) -> Any:
        """Create a bound method for a registered builder.

        The returned callable handles argument routing based on whether
        the builder requires an environment.

        Args:
            registration: BuilderRegistration from the registry.

        Returns:
            A callable that creates targets using the builder.
        """
        create_target = registration.create_target

        # Check if create_target accepts defined_at parameter
        import inspect

        sig = inspect.signature(create_target)
        accepts_defined_at = "defined_at" in sig.parameters

        # Wrap to inject project as first argument and capture caller location
        def builder_method(*args: Any, **kwargs: Any) -> Target:
            # Capture source location if builder accepts it
            if accepts_defined_at and "defined_at" not in kwargs:
                kwargs["defined_at"] = get_caller_location()
            return create_target(self, *args, **kwargs)

        # Copy the docstring if available
        if hasattr(create_target, "__doc__"):
            builder_method.__doc__ = create_target.__doc__

        return builder_method
