# SPDX-License-Identifier: MIT
"""Target abstraction with usage requirements.

A Target represents something that can be built (a library, program, etc.)
and carries "usage requirements" that propagate to consumers (CMake-style).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
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


__all__ = ["SourceSpec", "UsageRequirements", "Target", "ImportedTarget"]


class UsageRequirements:
    """Requirements that propagate from a target to its consumers.

    When target A depends on target B, B's public usage requirements
    are added to A's build. This enables CMake-style transitive
    dependency management.

    Stores named lists of values via attribute access. Any toolchain can
    define its own requirement names. C/C++ toolchains use include_dirs,
    defines, compile_flags, link_flags, link_libs. Other toolchains can
    use any names they need (e.g., python_packages, data_schemas).
    """

    _data: dict[str, list]

    def __init__(self, **kwargs: list) -> None:
        object.__setattr__(self, "_data", {})
        for k, v in kwargs.items():
            self._data[k] = list(v)

    def __getattr__(self, name: str) -> list:
        """Return the named list, creating it on first access."""
        data: dict[str, list] = object.__getattribute__(self, "_data")
        return data.setdefault(name, [])

    def __setattr__(self, name: str, value: list) -> None:  # type: ignore[override]
        if name.startswith("_"):
            object.__setattr__(self, name, value)
        else:
            if not isinstance(value, list):
                raise TypeError(
                    f"Usage requirement '{name}' must be a list, "
                    f"got {type(value).__name__}. "
                    f"Use target.public.{name}.append(value) to add items, "
                    f"or target.public.{name} = [value] to replace."
                )
            self._data[name] = value

    def merge(
        self,
        other: UsageRequirements,
        separated_arg_flags: frozenset[str] | None = None,
    ) -> None:
        """Merge another UsageRequirements into this one.

        Avoids duplicates while preserving order. For flags that take
        separate arguments (like -F path, -framework Foo), the
        flag+argument pair is treated as a unit.

        Args:
            other: The UsageRequirements to merge from.
            separated_arg_flags: Set of flags that take separate arguments.
                               If None, uses default (empty set).
        """
        for key, values in other._data.items():
            if not isinstance(values, list):
                raise TypeError(
                    f"Usage requirement '{key}' must be a list, "
                    f"got {type(values).__name__}. "
                    f'Use target.public.{key} = ["{values}"] or '
                    f'target.public.{key}.append("{values}").'
                )
            mine = self._data.setdefault(key, [])
            merge_flags(mine, values, separated_arg_flags)

    def clone(self) -> UsageRequirements:
        """Create a copy of this UsageRequirements."""
        result = UsageRequirements()
        for k, v in self._data.items():
            result._data[k] = list(v)
        return result

    def items(self) -> list[tuple[str, list]]:
        """Return all (name, list) pairs."""
        return list(self._data.items())

    def __bool__(self) -> bool:
        """True if any requirement list is non-empty."""
        return any(bool(v) for v in self._data.values())

    def __repr__(self) -> str:
        non_empty = {k: v for k, v in self._data.items() if v}
        if not non_empty:
            return "UsageRequirements()"
        items_str = ", ".join(f"{k}={v!r}" for k, v in non_empty.items())
        return f"UsageRequirements({items_str})"


# Characters not allowed in target names (would break ninja syntax or cause confusion).
# Allow: word chars, dots, plus, minus, forward slash (for subdirectory-style names).
_INVALID_TARGET_NAME_RE = re.compile(r"[^\w./+-]")


def _validate_target_name(name: str) -> None:
    """Validate that a target name is well-formed.

    Target names must be non-empty strings without spaces, slashes, or
    special characters that would break ninja build syntax.

    Raises:
        ValueError: If the name is empty or contains invalid characters.
    """
    if not name:
        raise ValueError("Target name must not be empty.")
    bad = _INVALID_TARGET_NAME_RE.findall(name)
    if bad:
        chars = "".join(sorted(set(bad)))
        raise ValueError(
            f"Target name {name!r} contains invalid characters: {chars!r}. "
            f"Target names may contain letters, digits, underscores, dots, "
            f"plus signs, hyphens, and forward slashes."
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
        nodes: All build nodes (intermediate + output), computed property.
        builder: Builder used to create this target.
        sources: Source nodes for this target.
        dependencies: Other targets this depends on.
        public: Usage requirements that propagate to dependents.
        private: Usage requirements for this target only.
        required_languages: Languages used by this target (set by toolchains).
        defined_at: Where this target was created in user code.
        target_type: Type of target (e.g., "program", "static_library").
        _env: Reference to the Environment used for building.
        intermediate_nodes: Intermediate build artifacts (e.g., object files).
        output_nodes: Final output nodes (library/program, populated by resolver).
        _resolved: Whether resolve() has been called on this target.
    """

    __slots__ = (
        "name",
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
        "intermediate_nodes",
        "output_nodes",
        "_resolved",
        # For install targets:
        "_install_nodes",
        # Custom output filename:
        "output_name",
        # Override platform prefix/suffix for output naming:
        "output_prefix",
        "output_suffix",
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
        # Implicit file deps from target.depends(file/path/node).
        # Applied to all build nodes (object + output) during resolve.
        "_extra_implicit_deps",
        # Implicit file deps with propagate=False (output nodes only).
        "_extra_implicit_deps_output_only",
        # Implicit target deps from target.depends(other_target).
        # Outputs become implicit deps on all build nodes; public usage
        # requirements propagate to compile steps (like link() but without
        # adding outputs to linker $in).
        "_implicit_target_deps",
        # Implicit target deps with propagate=False (output nodes only,
        # no usage requirement propagation).
        "_implicit_target_deps_output_only",
    )

    def __init__(
        self,
        name: str,
        *,
        target_type: str | None = None,
        builder: Builder | None = None,
        defined_at: SourceLocation | None = None,
    ) -> None:
        """Create a target.

        Args:
            name: Target name (e.g., "mylib", "myapp").
            target_type: Type of target (e.g., "program", "static_library").
                        Toolchains define their own type strings.
            builder: Builder to use for this target.
            defined_at: Source location where target was created.
        """
        _validate_target_name(name)
        self.name = name
        self.builder = builder
        self._sources: list[Node] = []
        self.dependencies: list[Target] = []
        self.public = UsageRequirements()
        self.private = UsageRequirements()
        self.required_languages: set[str] = set()
        self.defined_at = defined_at or get_caller_location()
        self._collected_requirements: UsageRequirements | None = None
        self.target_type: str | None = target_type
        self._env: Environment | None = None
        self._project: Project | None = None  # Set by Project when target is created
        self.intermediate_nodes: list[FileNode] = []
        self.output_nodes: list[FileNode] = []
        self._resolved: bool = False
        # For install targets:
        self._install_nodes: list[FileNode] = []
        # Custom output filename (overrides toolchain default naming):
        self.output_name: str | None = None
        # Override platform prefix/suffix (e.g., output_prefix="" to drop "lib"):
        self.output_prefix: str | None = None
        self.output_suffix: str | None = None
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
        # Implicit file deps (from target.depends(file/path/node))
        self._extra_implicit_deps: list[Node] = []
        self._extra_implicit_deps_output_only: list[Node] = []
        # Implicit target deps (from target.depends(other_target))
        self._implicit_target_deps: list[Target] = []
        self._implicit_target_deps_output_only: list[Target] = []

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

    @property
    def nodes(self) -> list[FileNode]:
        """All build nodes for this target (intermediate + output)."""
        return self.intermediate_nodes + self.output_nodes

    def link(self, *targets: Target) -> Target:
        """Add targets as dependencies (fluent API).

        The dependencies' public usage requirements will be applied
        when building this target.

        Args:
            *targets: Targets to depend on.

        Returns:
            self for method chaining.

        Raises:
            TypeError: If a non-Target argument is passed.
            ValueError: If a target tries to link itself.
            RuntimeError: If called after the target has been resolved.
        """
        if self._resolved:
            raise RuntimeError(
                f"Cannot modify target '{self.name}' after resolve(). "
                f"Call link() before project.resolve() or project.generate()."
            )
        for target in targets:
            if isinstance(target, (list, tuple)):
                raise TypeError(
                    "link() takes Target arguments, not a list. "
                    "Use target.link(a, b) instead of target.link([a, b])."
                )
            if not isinstance(target, Target):
                raise TypeError(
                    f"link() requires Target objects, got {type(target).__name__}. "
                    f"Use project.get_target(name) to look up a target by name."
                )
            if target is self:
                raise ValueError(f"Target '{self.name}' cannot link itself.")
            if target not in self.dependencies:
                self.dependencies.append(target)
        # Invalidate cached requirements
        self._collected_requirements = None
        return self

    def depends(
        self,
        *items: Target | Node | Path | str,
        propagate: bool = True,
    ) -> Target:
        """Add implicit dependencies (fluent API).

        Dependencies are added as implicit deps (after ``|`` in ninja)
        on this target's build nodes. They must be up to date before
        building this target, but their outputs are NOT passed as
        sources (not in ``$in``). Use ``target.link()`` for that.

        By default, deps are added to **all** build nodes — both
        intermediate and final output steps. This ensures generated
        files exist before any build step starts. For Target deps,
        public usage requirements also propagate, just like ``link()``.

        With ``propagate=False``, deps are only added to the final
        output nodes. Intermediate steps are unaffected.

        Args:
            *items: Files or targets to depend on. Strings and Paths are
                   converted to FileNodes via ``project.node()``.
            propagate: If True (default), apply to all build steps
                      (intermediate + output). If False, only output.

        Returns:
            self for method chaining.

        Example:
            gen = env.Command(
                target="generated.h",
                source="schema.json",
                command="python codegen.py $SOURCE -o $TARGET",
                restat=True,
            )
            # Generated header: use depends() so compile steps wait.
            app = project.Program("app", env, sources=["main.c"])
            app.depends(gen)
        """
        from pcons.core.node import FileNode, Node

        for item in items:
            if isinstance(item, Target):
                if item is self:
                    raise ValueError(f"Target '{self.name}' cannot depend on itself.")
                target_list = (
                    self._implicit_target_deps
                    if propagate
                    else self._implicit_target_deps_output_only
                )
                if item not in target_list:
                    target_list.append(item)
            else:
                file_list = (
                    self._extra_implicit_deps
                    if propagate
                    else self._extra_implicit_deps_output_only
                )
                if isinstance(item, Node):
                    file_list.append(item)
                else:
                    # str or Path — convert to FileNode via project
                    project = self._project
                    if project is not None:
                        file_list.append(project.node(item))
                    else:
                        file_list.append(
                            FileNode(item, defined_at=get_caller_location())
                        )

        return self

    def _apply_extra_implicit_deps(self) -> None:
        """Apply file-level implicit deps to build nodes.

        Propagated deps go on all nodes (intermediate + output).
        Output-only deps go on output nodes only.
        """
        all_nodes = self.intermediate_nodes + self.output_nodes
        for dep in self._extra_implicit_deps:
            for node in all_nodes:
                if dep not in node.implicit_deps:
                    node.implicit_deps.append(dep)
        for dep in self._extra_implicit_deps_output_only:
            for node in self.output_nodes:
                if dep not in node.implicit_deps:
                    node.implicit_deps.append(dep)

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

        Raises:
            TypeError: If sources is a string or bare Path instead of a list.
            RuntimeError: If called after the target has been resolved.

        Example:
            # Mix regular and generated sources
            generated = env.Command(target="gen.cpp", source="gen.y", command="...")
            target.add_sources([generated, "main.cpp", "util.cpp"], base=src_dir)
        """
        if self._resolved:
            raise RuntimeError(
                f"Cannot modify target '{self.name}' after resolve(). "
                f"Call add_sources() before project.resolve() or project.generate()."
            )
        if isinstance(sources, str):
            raise TypeError(
                f"add_sources() requires a list, got a string. "
                f'Use add_sources(["{sources}"]) or add_source("{sources}").'
            )
        if isinstance(sources, Path):
            raise TypeError(
                f"add_sources() requires a list, got a Path. "
                f"Use add_sources([{sources!r}]) or add_source({sources!r})."
            )
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

    def set_option(self, key: str, value: Any) -> Target:
        """Set a builder/toolchain option on this target (fluent API).

        Stores arbitrary key-value metadata that builders and toolchains
        can read during resolution.  The core does not interpret these
        values — their meaning is defined by the builder or toolchain.

        Common options (depends on target type and toolchain):

        - ``"install_name"`` — shared-library install name (macOS) or
          SONAME (Linux).  Set to ``""`` to disable the automatic default.

        Args:
            key: Option name.
            value: Option value.

        Returns:
            self for method chaining.
        """
        self._builder_data[key] = value
        return self

    def get_option(self, key: str, default: Any = None) -> Any:
        """Get a builder/toolchain option previously set with :meth:`set_option`.

        Args:
            key: Option name.
            default: Value to return if *key* was never set.

        Returns:
            The stored value, or *default*.
        """
        return self._builder_data.get(key, default)

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
            lines.append(f"  Type: {self.target_type}")
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
