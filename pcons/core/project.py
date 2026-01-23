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

from pcons.core.builder_registry import BuilderRegistry
from pcons.core.environment import Environment as Env
from pcons.core.graph import (
    collect_all_nodes,
    detect_cycles_in_targets,
    topological_sort_targets,
)
from pcons.core.node import AliasNode, DirNode, FileNode, Node
from pcons.core.paths import PathResolver
from pcons.core.target import Target
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
        "_resolved",
        "_path_resolver",
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
        self._resolved = False
        self._path_resolver = PathResolver(self.root_dir, self.build_dir)
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

    @property
    def path_resolver(self) -> PathResolver:
        """Get the path resolver for this project."""
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

        self._resolved = True

        # Check for graph output requests (set by CLI --graph/--mermaid options)
        self._output_graphs_if_requested()

    def _output_graphs_if_requested(self) -> None:
        """Output dependency graphs if requested via PCONS_GRAPH/PCONS_MERMAID env vars."""
        import os
        import tempfile

        # DOT format graph
        graph_path = os.environ.get("PCONS_GRAPH")
        if graph_path:
            from pcons.generators.dot import DotGenerator

            if graph_path == "-":
                # Write to stdout
                print("# DOT dependency graph")
                gen = DotGenerator(output_filename="deps.dot")
                with tempfile.TemporaryDirectory() as tmpdir:
                    gen.generate(self, Path(tmpdir))
                    dot_content = (Path(tmpdir) / "deps.dot").read_text()
                    print(dot_content)
            else:
                # Write to file
                output_path = Path(graph_path)
                gen = DotGenerator(output_filename=output_path.name)
                gen.generate(self, output_path.parent)
                logger.info("Wrote DOT graph to %s", graph_path)

        # Mermaid format graph
        mermaid_path = os.environ.get("PCONS_MERMAID")
        if mermaid_path:
            from pcons.generators.mermaid import MermaidGenerator

            if mermaid_path == "-":
                # Write to stdout
                print("# Mermaid dependency graph")
                gen = MermaidGenerator(output_filename="deps.mmd")
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
        return env.Command(target=target, source=source, command=command, name=name)

    def __str__(self) -> str:
        """User-friendly string representation for debugging."""
        lines = [f"Project: {self.name}"]
        lines.append(f"  Root: {self.root_dir}")
        lines.append(f"  Build: {self.build_dir}")
        lines.append(f"  Targets: {len(self._targets)}")
        for target in list(self._targets.values())[:5]:
            target_type = target.target_type.name if target.target_type else "unknown"
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

    def __getattr__(self, name: str) -> Any:
        """Dynamic attribute access for registered builders.

        Allows registered builders to be called as methods on Project instances:
            project.InstallSymlink(...)  # if InstallSymlink is registered

        Args:
            name: Attribute name to look up.

        Returns:
            A bound method that calls the builder's create_target function.

        Raises:
            AttributeError: If the attribute is not a registered builder.
        """
        # Check if it's a registered builder
        registration = BuilderRegistry.get(name)
        if registration is not None:
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
