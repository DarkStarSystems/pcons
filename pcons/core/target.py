# SPDX-License-Identifier: MIT
"""Target abstraction with usage requirements.

A Target represents something that can be built (a library, program, etc.)
and carries "usage requirements" that propagate to consumers (CMake-style).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pcons.util.source_location import SourceLocation, get_caller_location

if TYPE_CHECKING:
    from pcons.core.builder import Builder
    from pcons.core.environment import Environment
    from pcons.core.node import FileNode, Node

# Valid target types
TargetType = Literal[
    "static_library",
    "shared_library",
    "program",
    "interface",  # Header-only library
    "object",  # Object files only (no linking)
]


@dataclass
class UsageRequirements:
    """Requirements that propagate from a target to its consumers.

    When target A depends on target B, B's public usage requirements
    are added to A's build. This enables CMake-style transitive
    dependency management.
    """

    include_dirs: list[Path] = field(default_factory=list)
    link_libs: list[str] = field(default_factory=list)
    defines: list[str] = field(default_factory=list)
    compile_flags: list[str] = field(default_factory=list)
    link_flags: list[str] = field(default_factory=list)

    def merge(self, other: UsageRequirements) -> None:
        """Merge another UsageRequirements into this one.

        Avoids duplicates while preserving order.
        """
        for inc_dir in other.include_dirs:
            if inc_dir not in self.include_dirs:
                self.include_dirs.append(inc_dir)
        for lib in other.link_libs:
            if lib not in self.link_libs:
                self.link_libs.append(lib)
        for define in other.defines:
            if define not in self.defines:
                self.defines.append(define)
        for cflag in other.compile_flags:
            if cflag not in self.compile_flags:
                self.compile_flags.append(cflag)
        for lflag in other.link_flags:
            if lflag not in self.link_flags:
                self.link_flags.append(lflag)

    def clone(self) -> UsageRequirements:
        """Create a copy of this UsageRequirements."""
        return UsageRequirements(
            include_dirs=list(self.include_dirs),
            link_libs=list(self.link_libs),
            defines=list(self.defines),
            compile_flags=list(self.compile_flags),
            link_flags=list(self.link_flags),
        )


class Target:
    """A named build target with usage requirements.

    Targets are the high-level abstraction for things like libraries
    and programs. They carry "usage requirements" - compile/link flags
    that propagate to targets that depend on them.

    Usage requirements have two scopes:
    - PUBLIC: Apply to this target AND propagate to dependents
    - PRIVATE: Apply only to this target

    Example:
        mylib = project.Library("mylib", sources=["lib.cpp"])
        mylib.public.include_dirs.append(Path("include"))
        mylib.private.defines.append("MYLIB_BUILDING")

        app = project.Program("app", sources=["main.cpp"])
        app.link(mylib)  # Gets mylib's public include_dirs

    Attributes:
        name: Target name.
        nodes: Output nodes created by building this target.
        builder: Builder used to create this target.
        sources: Source nodes for this target.
        dependencies: Other targets this depends on.
        public: Usage requirements that propagate to dependents.
        private: Usage requirements for this target only.
        required_languages: Languages needed to build/link this target.
        defined_at: Where this target was created in user code.
        target_type: Type of target (static_library, shared_library, program, interface).
        _env: Reference to the Environment used for building.
        object_nodes: Compiled object nodes (populated by resolver).
        output_nodes: Final output nodes (library/program, populated by resolver).
        _resolved: Whether resolve() has been called on this target.
    """

    __slots__ = (
        "name",
        "nodes",
        "builder",
        "sources",
        "dependencies",
        "public",
        "private",
        "required_languages",
        "defined_at",
        "_collected_requirements",
        # NEW for target-centric build model:
        "target_type",
        "_env",
        "object_nodes",
        "output_nodes",
        "_resolved",
    )

    def __init__(
        self,
        name: str,
        *,
        target_type: TargetType | None = None,
        builder: Builder | None = None,
        defined_at: SourceLocation | None = None,
    ) -> None:
        """Create a target.

        Args:
            name: Target name (e.g., "mylib", "myapp").
            target_type: Type of target (static_library, shared_library, program, interface).
            builder: Builder to use for this target.
            defined_at: Source location where target was created.
        """
        self.name = name
        self.nodes: list[Node] = []
        self.builder = builder
        self.sources: list[Node] = []
        self.dependencies: list[Target] = []
        self.public = UsageRequirements()
        self.private = UsageRequirements()
        self.required_languages: set[str] = set()
        self.defined_at = defined_at or get_caller_location()
        self._collected_requirements: UsageRequirements | None = None
        # NEW for target-centric build model:
        self.target_type: TargetType | None = target_type
        self._env: Environment | None = None
        self.object_nodes: list[FileNode] = []
        self.output_nodes: list[FileNode] = []
        self._resolved: bool = False

    def link(self, *targets: Target) -> None:
        """Add targets as dependencies.

        The dependencies' public usage requirements will be applied
        when building this target.

        Args:
            *targets: Targets to depend on.
        """
        for target in targets:
            if target not in self.dependencies:
                self.dependencies.append(target)
        # Invalidate cached requirements
        self._collected_requirements = None

    def add_source(self, node: Node) -> None:
        """Add a source node to this target."""
        self.sources.append(node)

    def add_sources(self, nodes: list[Node]) -> None:
        """Add multiple source nodes to this target."""
        self.sources.extend(nodes)

    def collect_usage_requirements(self) -> UsageRequirements:
        """Collect transitive public requirements from all dependencies.

        Returns a UsageRequirements containing this target's private
        requirements plus all public requirements from the dependency
        tree.

        Returns:
            Combined usage requirements.
        """
        if self._collected_requirements is not None:
            return self._collected_requirements

        # Start with this target's private requirements
        result = self.private.clone()

        # Merge in public requirements from all dependencies (DFS)
        visited: set[str] = set()
        self._collect_from_deps(result, visited)

        self._collected_requirements = result
        return result

    def _collect_from_deps(
        self, result: UsageRequirements, visited: set[str]
    ) -> None:
        """Recursively collect public requirements from dependencies."""
        for dep in self.dependencies:
            if dep.name in visited:
                continue
            visited.add(dep.name)

            # Merge this dependency's public requirements
            result.merge(dep.public)

            # Recursively get transitive requirements
            dep._collect_from_deps(result, visited)

    def get_all_languages(self) -> set[str]:
        """Get all languages required by this target and its dependencies.

        Used to determine which linker to use.

        Returns:
            Set of language names (e.g., {'c', 'cxx'}).
        """
        languages = set(self.required_languages)
        visited: set[str] = {self.name}

        for dep in self.dependencies:
            if dep.name not in visited:
                visited.add(dep.name)
                languages.update(dep.get_all_languages())

        return languages

    def transitive_dependencies(self) -> list[Target]:
        """Return all dependencies transitively (DFS, no duplicates).

        Returns dependencies in the order they are discovered via DFS,
        which means dependencies are listed before their dependents.

        Returns:
            List of all transitive dependencies (not including self).
        """
        result: list[Target] = []
        visited: set[str] = set()

        def _collect(target: Target) -> None:
            for dep in target.dependencies:
                if dep.name not in visited:
                    visited.add(dep.name)
                    _collect(dep)
                    result.append(dep)

        _collect(self)
        return result

    def __repr__(self) -> str:
        deps = ", ".join(d.name for d in self.dependencies)
        return f"Target({self.name!r}, deps=[{deps}])"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Target):
            return NotImplemented
        return self.name == other.name

    def __hash__(self) -> int:
        return hash(self.name)


class ImportedTarget(Target):
    """A target representing an external dependency.

    ImportedTargets are created from package descriptions or pkg-config.
    They provide usage requirements but aren't built by pcons.

    Example:
        zlib = project.find_package("zlib")
        app = project.Program("app", sources=["main.cpp"])
        app.link(zlib)  # Gets zlib's include/link flags
    """

    __slots__ = ("is_imported", "package_name", "version")

    def __init__(
        self,
        name: str,
        *,
        package_name: str | None = None,
        version: str | None = None,
        defined_at: SourceLocation | None = None,
    ) -> None:
        """Create an imported target.

        Args:
            name: Target name (often same as package name).
            package_name: Name of the package this came from.
            version: Package version if known.
            defined_at: Source location where created.
        """
        super().__init__(name, defined_at=defined_at)
        self.is_imported = True
        self.package_name = package_name or name
        self.version = version

    def __repr__(self) -> str:
        version = f" v{self.version}" if self.version else ""
        return f"ImportedTarget({self.name!r}{version})"
