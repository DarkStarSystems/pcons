# SPDX-License-Identifier: MIT
"""Target abstraction with usage requirements.

A Target represents something that can be built (a library, program, etc.)
and carries "usage requirements" that propagate to consumers (CMake-style).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pcons.core.flags import merge_flags

# Import SourceSpec from centralized types module
from pcons.core.types import SourceSpec
from pcons.util.source_location import SourceLocation, get_caller_location

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from pcons.core.builder import Builder
    from pcons.core.environment import Environment
    from pcons.core.node import FileNode, Node
    from pcons.core.project import Project


class TargetType(StrEnum):
    """Valid target types.

    Using StrEnum allows these values to be used directly in string comparisons
    while providing type safety and preventing typos.
    """

    STATIC_LIBRARY = "static_library"
    SHARED_LIBRARY = "shared_library"
    PROGRAM = "program"
    INTERFACE = "interface"  # Header-only library
    OBJECT = "object"  # Object files only (no linking)
    ARCHIVE = "archive"  # Tar/Zip archives
    COMMAND = "command"  # Generic command output


__all__ = ["SourceSpec", "TargetType", "UsageRequirements", "Target", "ImportedTarget"]


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

    def merge(
        self,
        other: UsageRequirements,
        separated_arg_flags: frozenset[str] | None = None,
    ) -> None:
        """Merge another UsageRequirements into this one.

        Avoids duplicates while preserving order. For compiler and linker
        flags, handles flags that take separate arguments (like -F path,
        -framework Foo) by treating the flag+argument pair as a unit.

        Args:
            other: The UsageRequirements to merge from.
            separated_arg_flags: Set of flags that take separate arguments.
                               If None, uses default (empty set).
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
        # Use flag-aware merge for compile and link flags
        merge_flags(self.compile_flags, other.compile_flags, separated_arg_flags)
        merge_flags(self.link_flags, other.link_flags, separated_arg_flags)

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
        "_sources",
        "dependencies",
        "public",
        "private",
        "required_languages",
        "defined_at",
        "_collected_requirements",
        # NEW for target-centric build model:
        "target_type",
        "_env",
        "_project",
        "object_nodes",
        "output_nodes",
        "_resolved",
        # For install targets:
        "_install_nodes",
        # Custom output filename:
        "output_name",
        # Lazy source resolution (for Install, etc.):
        "_pending_sources",
        # Build info for archive and command targets:
        "_build_info",
        # Generic builder support (extensible builder architecture):
        "_builder_name",  # Name of the builder that created this target
        # Builder-specific data dict. Contains:
        #   - "post_build_commands": list[str] - Shell commands run after target is built
        #   - "auxiliary_inputs": list[tuple[FileNode, str, AuxiliaryInputHandler]]
        #       Files passed to linker with flags and handler info
        #   - Other builder-specific data (dest_dir, compression, etc.)
        "_builder_data",
    )

    def __init__(
        self,
        name: str,
        *,
        target_type: TargetType | str | None = None,
        builder: Builder | None = None,
        defined_at: SourceLocation | None = None,
    ) -> None:
        """Create a target.

        Args:
            name: Target name (e.g., "mylib", "myapp").
            target_type: Type of target (TargetType enum or string like "static_library").
                        Using TargetType enum constants is preferred for type safety.
            builder: Builder to use for this target.
            defined_at: Source location where target was created.
        """
        self.name = name
        self.nodes: list[Node] = []
        self.builder = builder
        self._sources: list[Node] = []
        self.dependencies: list[Target] = []
        self.public = UsageRequirements()
        self.private = UsageRequirements()
        self.required_languages: set[str] = set()
        self.defined_at = defined_at or get_caller_location()
        self._collected_requirements: UsageRequirements | None = None
        # NEW for target-centric build model:
        # Convert string target_type to TargetType enum if provided as string
        if isinstance(target_type, str):
            self.target_type: TargetType | None = TargetType(target_type)
        else:
            self.target_type = target_type
        self._env: Environment | None = None
        self._project: Project | None = None  # Set by Project when target is created
        self.object_nodes: list[FileNode] = []
        self.output_nodes: list[FileNode] = []
        self._resolved: bool = False
        # For install targets:
        self._install_nodes: list[FileNode] = []
        # Custom output filename (overrides toolchain default naming):
        self.output_name: str | None = None
        # Lazy source resolution (for Install, etc.):
        # Sources that need resolution after main resolve phase
        self._pending_sources: list[Target | Node | Path | str] | None = None
        # Build info for archive and command targets
        self._build_info: dict[str, Any] | None = None
        # Generic builder support (extensible builder architecture)
        self._builder_name: str | None = None
        # Builder-specific data dict, initialized to empty dict (not None)
        # Contains: post_build_commands, auxiliary_inputs, and builder-specific data
        self._builder_data: dict[str, Any] = {}

    @property
    def sources(self) -> list[Node]:
        """Get the list of source nodes for this target.

        This includes both immediate sources (_sources) and resolved
        Target sources from _pending_sources. Target sources are only
        included after those Targets have been resolved (output_nodes populated).

        Note: This returns a new list. Use add_source() or add_sources() to
        modify the source list.
        """
        result = list(self._sources)

        # Add output_nodes from any resolved Target sources
        if self._pending_sources:
            for source in self._pending_sources:
                if isinstance(source, Target) and source.output_nodes:
                    result.extend(source.output_nodes)

        return result

    @sources.setter
    def sources(self, value: list[Node]) -> None:
        """Raise an error on direct assignment to sources.

        Direct assignment to .sources is not allowed. Use add_source() or
        add_sources() instead. This ensures consistent source management
        and proper handling of Target sources (which need deferred resolution).

        Raises:
            AttributeError: Always, with guidance on proper methods to use.
        """
        raise AttributeError(
            f"Cannot assign directly to {self.name}.sources. "
            f"Use add_source() or add_sources() instead. "
            f"Example: target.add_sources({value!r})"
        )

    def link(self, *targets: Target) -> Target:
        """Add targets as dependencies (fluent API).

        The dependencies' public usage requirements will be applied
        when building this target.

        Args:
            *targets: Targets to depend on.

        Returns:
            self for method chaining.
        """
        for target in targets:
            if target not in self.dependencies:
                self.dependencies.append(target)
        # Invalidate cached requirements
        self._collected_requirements = None
        return self

    def add_source(self, source: Target | Node | Path | str) -> Target:
        """Add a source to this target (fluent API).

        Args:
            source: Source file (Target, Node, Path, or string path).
                   If a Target is passed, its output files become sources
                   after that Target is resolved.

        Returns:
            self for method chaining.

        Example:
            # Add a generated source file
            generated = env.Command(target="gen.cpp", source="gen.y", command="...")
            program.add_source(generated)
        """
        if isinstance(source, Target):
            # Store Target sources for deferred resolution
            if self._pending_sources is None:
                self._pending_sources = []
            self._pending_sources.append(source)
            # Add as dependency to ensure correct build order
            if source not in self.dependencies:
                self.dependencies.append(source)
        else:
            node = self._to_node(source)
            self._sources.append(node)
        return self

    def add_sources(
        self,
        sources: Sequence[Target | Node | Path | str],
        *,
        base: Path | str | None = None,
    ) -> Target:
        """Add multiple sources to this target (fluent API).

        Args:
            sources: Source files (Targets, Nodes, Paths, or string paths).
                    If Targets are included, their output files become sources
                    after those Targets are resolved.
            base: Optional base directory for relative paths (only applies
                  to Path and string sources, not Targets).

        Returns:
            self for method chaining.

        Example:
            # Mix regular and generated sources
            generated = env.Command(target="gen.cpp", source="gen.y", command="...")
            target.add_sources([generated, "main.cpp", "util.cpp"], base=src_dir)
        """
        base_path = Path(base) if base else None
        for source in sources:
            if isinstance(source, Target):
                # Store Target sources for deferred resolution
                if self._pending_sources is None:
                    self._pending_sources = []
                self._pending_sources.append(source)
                # Add as dependency to ensure correct build order
                if source not in self.dependencies:
                    self.dependencies.append(source)
            else:
                if base_path and isinstance(source, (str, Path)):
                    path = Path(source)
                    if not path.is_absolute():
                        source = base_path / path
                node = self._to_node(source)
                self._sources.append(node)
        return self

    def _to_node(self, source: Node | Path | str) -> Node:
        """Convert a source specification to a Node."""
        from pcons.core.node import FileNode
        from pcons.core.node import Node as NodeClass

        if isinstance(source, NodeClass):
            return source
        path = Path(source)
        # Use project's node() if available for deduplication
        if self._project is not None:
            node: Node = self._project.node(path)
            return node
        return FileNode(path)

    # Fluent API for usage requirements

    def public_includes(self, dirs: list[Path | str]) -> Target:
        """Add public include directories (fluent API).

        These directories propagate to targets that depend on this one.

        Args:
            dirs: Include directories.

        Returns:
            self for method chaining.
        """
        for d in dirs:
            self.public.include_dirs.append(Path(d))
        return self

    def public_defines(self, defines: list[str]) -> Target:
        """Add public preprocessor defines (fluent API).

        These defines propagate to targets that depend on this one.

        Args:
            defines: Preprocessor defines (e.g., ["FOO", "BAR=1"]).

        Returns:
            self for method chaining.
        """
        self.public.defines.extend(defines)
        return self

    def public_flags(self, flags: list[str]) -> Target:
        """Add public compiler flags (fluent API).

        These flags propagate to targets that depend on this one.

        Args:
            flags: Compiler flags.

        Returns:
            self for method chaining.
        """
        self.public.compile_flags.extend(flags)
        return self

    def private_includes(self, dirs: list[Path | str]) -> Target:
        """Add private include directories (fluent API).

        These directories are only used when building this target.

        Args:
            dirs: Include directories.

        Returns:
            self for method chaining.
        """
        for d in dirs:
            self.private.include_dirs.append(Path(d))
        return self

    def private_defines(self, defines: list[str]) -> Target:
        """Add private preprocessor defines (fluent API).

        These defines are only used when building this target.

        Args:
            defines: Preprocessor defines (e.g., ["FOO", "BAR=1"]).

        Returns:
            self for method chaining.
        """
        self.private.defines.extend(defines)
        return self

    def private_flags(self, flags: list[str]) -> Target:
        """Add private compiler flags (fluent API).

        These flags are only used when building this target.

        Args:
            flags: Compiler flags.

        Returns:
            self for method chaining.
        """
        self.private.compile_flags.extend(flags)
        return self

    def post_build(self, command: str) -> Target:
        """Add a post-build command (fluent API).

        Post-build commands are shell commands that run after the target
        is built. Commands support variable substitution:
        - $out: The primary output file path
        - $in: The input files (space-separated)

        Commands run in the order they are added.

        Args:
            command: Shell command to run after building.

        Returns:
            self for method chaining.

        Example:
            plugin = project.SharedLibrary("myplugin", env)
            plugin.post_build("install_name_tool -add_rpath @loader_path $out")
            plugin.post_build("codesign --sign - $out")
        """
        if "post_build_commands" not in self._builder_data:
            self._builder_data["post_build_commands"] = []
        self._builder_data["post_build_commands"].append(command)
        return self

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

    def _collect_from_deps(self, result: UsageRequirements, visited: set[str]) -> None:
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

    def __str__(self) -> str:
        """User-friendly string representation for debugging."""
        lines = [f"Target: {self.name}"]
        if self.target_type:
            lines.append(f"  Type: {self.target_type.name}")
        if self.defined_at:
            lines.append(f"  Defined at: {self.defined_at}")
        if self._sources:
            lines.append(f"  Sources: {len(self._sources)} files")
            for src in self._sources[:5]:  # Show first 5
                lines.append(f"    - {src.name}")
            if len(self._sources) > 5:
                lines.append(f"    ... and {len(self._sources) - 5} more")
        if self.output_nodes:
            lines.append(f"  Outputs: {[str(n.path) for n in self.output_nodes]}")
        if self.dependencies:
            lines.append(f"  Dependencies: {[d.name for d in self.dependencies]}")
        if self.public.include_dirs:
            lines.append(f"  Public includes: {self.public.include_dirs}")
        if self.public.defines:
            lines.append(f"  Public defines: {self.public.defines}")
        return "\n".join(lines)

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
