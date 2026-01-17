# SPDX-License-Identifier: MIT
"""Project container for pcons builds.

The Project is the top-level container that holds all environments,
targets, and nodes for a build. It provides node deduplication and
serves as the context for build descriptions.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from pcons.core.environment import Environment as Env
from pcons.core.graph import (
    collect_all_nodes,
    detect_cycles_in_targets,
    topological_sort_targets,
)
from pcons.core.node import AliasNode, DirNode, FileNode, Node
from pcons.core.target import Target
from pcons.util.source_location import SourceLocation, get_caller_location

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
        **kwargs: Any,
    ) -> Env:
        """Create and register a new environment.

        Args:
            toolchain: Optional toolchain to initialize with.
            **kwargs: Additional variables to set on the environment.

        Returns:
            A new Environment attached to this project.
        """
        env = Env(
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
            raise TypeError(f"Path {path} is registered as {type(node).__name__}, not FileNode")
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
            raise TypeError(f"Path {path} is registered as {type(node).__name__}, not DirNode")
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
                alias.add_targets(t.nodes)
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

    def resolve(self) -> None:
        """Resolve all targets.

        This processes all targets in dependency order, computing effective
        requirements and creating the necessary nodes for compilation and linking.

        After resolution, each target's object_nodes and output_nodes are populated.
        """
        from pcons.core.resolver import Resolver

        resolver = Resolver(self)
        resolver.resolve()

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
        target = Target(name, target_type="static_library", defined_at=get_caller_location())
        target._env = env
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
        target = Target(name, target_type="shared_library", defined_at=get_caller_location())
        target._env = env
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
        target = Target(name, target_type="program", defined_at=get_caller_location())
        target._env = env
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
        target = Target(name, target_type="interface", defined_at=get_caller_location())
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
        target = Target(name, target_type="object", defined_at=get_caller_location())
        target._env = env
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

    def __repr__(self) -> str:
        return (
            f"Project({self.name!r}, "
            f"targets={len(self._targets)}, "
            f"envs={len(self._environments)})"
        )
