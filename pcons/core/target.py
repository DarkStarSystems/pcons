# SPDX-License-Identifier: MIT
"""Target abstraction with usage requirements.

A Target represents something that can be built (a library, program, etc.)
and carries "usage requirements" that propagate to consumers (CMake-style).
"""

from __future__ import annotations

import functools
import logging
import re
import sys
import warnings
from collections import UserList
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeAlias, TypeVar

if sys.version_info >= (3, 13):
    from warnings import deprecated
else:

    def deprecated(msg: str):  # type: ignore[no-redef]
        def decorator(func):
            @functools.wraps(func)
            def wrapper(*args, **kwargs):
                warnings.warn(
                    f"{func.__name__} is deprecated: {msg}",
                    DeprecationWarning,
                    stacklevel=2,
                )
                return func(*args, **kwargs)

            return wrapper

        return decorator


from pcons.core.flags import merge_flags

# Import SourceSpec from centralized types module
from pcons.core.types import SourceSpec
from pcons.util.source_location import SourceLocation, get_caller_location

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from pcons.core._usage_requirements_stubs import _UsageRequirementsStubs
    from pcons.core.builder import Builder
    from pcons.core.environment import Environment
    from pcons.core.node import BuildInfo, FileNode, Node
    from pcons.core.paths import PathResolver
    from pcons.core.project import Project
else:
    # At runtime, UsageRequirements inherits from `object`. The mixin only
    # provides typed declarations for static analysis. __getattr__ continues
    # to lazily create per-name lists as before.
    _UsageRequirementsStubs = object


__all__ = ["SourceSpec", "UsageRequirements", "Target", "ImportedTarget"]

_T = TypeVar("_T")
ListLike: TypeAlias = list[_T] | UserList[_T]


class UniqueList(UserList[_T]):
    def __init__(self, initlist: ListLike[_T] | None = None) -> None:
        super().__init__(initlist or [])

    def append(self, item: _T):
        if item not in self.data:
            self.data.append(item)

    def extend(self, other: Iterable[_T]):
        for item in other:
            self.append(item)


class ValidatedUniqueList(UniqueList[_T]):
    def __init__(
        self,
        initlist: ListLike[_T] | None = None,
        on_append: Callable[[_T], None] | None = None,
    ) -> None:
        super().__init__(initlist)
        self._on_append = on_append

    def append(self, item: _T):
        if self._on_append is not None:
            self._on_append(item)
        super().append(item)


class UsageRequirements(_UsageRequirementsStubs):
    """Requirements that propagate from a target to its consumers.

    When target A depends on target B, B's public usage requirements
    are added to A's build. This enables CMake-style transitive
    dependency management.

    Stores named lists of values via attribute access. Any toolchain can
    define its own requirement names. C/C++ toolchains use include_dirs,
    defines, compile_flags, link_flags, link_libs. Other toolchains can
    use any names they need (e.g., python_packages, data_schemas).

    A field may use a special list type (``UniqueList`` dedup, or
    ``ValidatedUniqueList`` whose ``on_append`` hook enforces invariants and
    invalidates caches). Whole-list assignment preserves those semantics:
    ``__setattr__`` replaces an existing list's *contents* in place (clear +
    extend through ``append``), so ``reqs.link_libs = [a, b]`` behaves like
    repeated ``.append()`` instead of swapping in a plain list.
    """

    _data: dict[str, list[Any] | UserList[Any]]

    def __init__(self, **kwargs: list[Any] | UserList[Any]) -> None:
        object.__setattr__(self, "_data", {})
        for k, v in kwargs.items():
            self._data[k] = v

    def __getattr__(self, name: str) -> list[Any] | UserList[Any]:
        """Return the named list, creating it on first access."""
        data: dict[str, list[Any] | UserList[Any]] = object.__getattribute__(
            self, "_data"
        )
        return data.setdefault(name, [])

    def __setattr__(self, name: str, value: list[Any] | UserList[Any]) -> None:  # type: ignore[override]
        if name.startswith("_"):
            object.__setattr__(self, name, value)
        else:
            if not isinstance(value, (list, UserList)):
                raise TypeError(
                    f"Usage requirement '{name}' must be a list, "
                    f"got {type(value).__name__}. "
                    f"Use target.public.{name}.append(value) to add items, "
                    f"or target.public.{name} = [value] to replace."
                )
            existing = self._data.get(name)
            if isinstance(existing, UserList):
                # Replace contents in place so the existing list type's
                # behavior (UniqueList dedup, ValidatedUniqueList.on_append)
                # is preserved across assignment.
                existing.clear()
                existing.extend(value)
            else:
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
            # Reuse the source list's concrete type so dedup behaviour carries over.
            # A ValidatedUniqueList is rebuilt without its validator: the
            # validator is bound to the owning Target (self-link / post-resolve checks),
            # and merge() only targets a detached snapshot, so it has nothing to guard.
            mine = self._data.setdefault(key, type(values)())
            merge_flags(mine, values, separated_arg_flags)

    def clone(self) -> UsageRequirements:
        """Create a copy of this UsageRequirements.

        Each list keeps its concrete type (so dedup behaviour is preserved), but
        a ValidatedUniqueList is rebuilt without its validator. That validator is
        bound to the owning Target. A clone is a detached snapshot that is read,
        not user-mutated, so the owner-specific guard does not apply to it.
        """
        result = UsageRequirements()
        for k, v in self._data.items():
            result._data[k] = type(v)(v)
        return result

    def items(self) -> list[tuple[str, list[Any] | UserList[Any]]]:
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


def split_qualified_name(
    name: str, raise_on_invalid: bool = True
) -> tuple[str | None, str]:
    """Split a qualified name into (project, target).

    A qualified name contains a project qualifier, in the form "project::target".
    If the name is not qualified, returns (None, name).

    Args:
        name: The qualified or unqualified name to split.
    Returns:
        A tuple of (project, target) where project is None if not qualified.
    """
    parts = name.split("::")
    count = len(parts)
    if count == 2:
        return parts[0], parts[1]
    elif count == 1:
        return None, parts[0]
    else:
        if raise_on_invalid:
            raise ValueError(
                f"Invalid qualified name: {name!r}. Too many '::' separators."
            )
        return None, name


def is_qualified_name(name: str) -> bool:
    """Check if a name is a qualified name.

    A qualified name contains a project qualifier, in the form "project::target".
    """
    project, _target = split_qualified_name(name, raise_on_invalid=False)
    return project is not None


def _make_default_requirements(
    link_libs_validator: Callable[[Any], None],
) -> UsageRequirements:
    """Create a default UsageRequirements with standard C/C++ fields."""
    reqs = UsageRequirements()
    reqs.defines = UniqueList([])
    reqs.include_dirs = UniqueList([])
    reqs.link_dirs = UniqueList([])
    # compile_flags/link_flags are plain lists, NOT UniqueList: token-level
    # dedup corrupts paired flags (-framework Foo -framework Bar, -Xlinker, -arch,
    # repeated -L/-F). Flag dedup is pair-aware and happens via merge_flags() when
    # usage requirements are merged. Direct appends are preserved verbatim.
    reqs.compile_flags = []
    reqs.link_flags = []
    reqs.link_libs = ValidatedUniqueList([], on_append=link_libs_validator)
    return reqs


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
        "_dependencies",
        "public",
        "private",
        "required_languages",
        "defined_at",
        "_collected_requirements",
        # NEW for target-centric build model:
        "target_type",
        "_env",
        "__project",
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
        "_subdir",
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
        self._dependencies: list[Target] = []
        self.public = _make_default_requirements(self.__link_libs_validator)
        self.private = _make_default_requirements(self.__link_libs_validator)
        self.required_languages: set[str] = set()
        self.defined_at = defined_at or get_caller_location()
        self._collected_requirements: UsageRequirements | None = None
        self.target_type: str | None = target_type
        self._env: Environment | None = None
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
        self._build_info: BuildInfo | dict[str, Any] | None = None
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

        from pcons.core.project import Project

        project = Project.current()

        self.__project = project
        self._subdir = project._subdir

        if self._env is None:
            # default to the last environment in the project, if available
            self._env = project.environments[-1] if project.environments else None

        self.__project._add_target(self)

    @property
    def project(self) -> Project:
        """Get the project this target belongs to."""
        return self.__project

    @property
    def qualified_name(self) -> str:
        """Get the qualified name.

        Returns:
            The qualified name, in the form "<project>::<target>".
        """
        return f"{self.project.name}::{self.name}"

    @property
    def dependencies(self):
        """Get the list of Target dependencies for this target."""
        linked_public_targets = [
            t for t in self.public.link_libs if isinstance(t, Target)
        ]
        linked_private_targets = [
            t for t in self.private.link_libs if isinstance(t, Target)
        ]
        return (*self._dependencies, *linked_public_targets, *linked_private_targets)

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
    def build_dir(self) -> Path:
        if self._subdir:
            return self.project.build_dir / self._subdir
        return self.project.build_dir

    @property
    def source_dir(self) -> Path:
        if self._subdir:
            return self.project.root_dir / self._subdir
        return self.project.root_dir

    @property
    def path_resolver(self) -> PathResolver:
        """Get the Path resolver for this target's project."""
        if self._subdir:
            return self.project.path_resolver.subdir(self._subdir)
        return self.project.path_resolver

    @property
    def nodes(self) -> list[FileNode]:
        """All build nodes for this target (intermediate + output)."""
        return self.intermediate_nodes + self.output_nodes

    def __link_libs_validator(self, target: Target):
        if self._resolved:
            raise RuntimeError(f"Cannot modify target '{self.name}' after resolve(). ")
        if target is self:
            raise ValueError(f"Target '{self.name}' cannot link itself.")
        # Invalidate cached requirements
        self._collected_requirements = None

    @deprecated("Use target.{public,private}.link_libs instead")
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
                f"Add link_libs before project.resolve() or project.generate()."
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
            if target not in self.public.link_libs:
                self.public.link_libs.append(target)
        # Invalidate cached requirements
        self._collected_requirements = None
        return self

    def add_dependency(self, *targets: Target) -> Target:
        """Add Targets as build dependencies of this target.

        Each becomes a full dependency: it is included in
        ``transitive_dependencies()`` and ``dependencies``, so its public
        usage requirements propagate here. Unlike ``link_libs``, this does not
        treat the targets as libraries to link, use ``target.{public,private}
        .link_libs`` for that. Duplicates are ignored.

        Args:
            *targets: Targets to depend on.

        Returns:
            self for method chaining.

        Raises:
            RuntimeError: If called after the target has been resolved.
        """
        if self._resolved:
            raise RuntimeError(
                f"Cannot modify target '{self.name}' after resolve(). "
                f"Add dependencies before project.resolve() or project.generate()."
            )
        for target in targets:
            if target not in self._dependencies:
                self._dependencies.append(target)
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
                    project = self.project
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
            if source not in self._dependencies:
                self._dependencies.append(source)
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
                if source not in self._dependencies:
                    self._dependencies.append(source)
            else:
                if base_path and isinstance(source, (str, Path)):
                    path = Path(source)
                    if not path.is_absolute():
                        source = base_path / path
                # Only join subdir when source is a string or Path. If it's
                # already a Node, leave it alone.
                if self._subdir and isinstance(source, (str, Path)):
                    source = Path(self._subdir) / source
                node = self._to_node(source)
                self._sources.append(node)
        return self

    def _to_node(self, source: Node | Path | str) -> Node:
        """Convert a source specification to a Node."""
        from pcons.core.node import Node as NodeClass

        if isinstance(source, NodeClass):
            return source
        path = Path(source)
        return self.project.node(path)

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
        for dep in self.transitive_dependencies():
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

        def direct_deps(target: Target, *, include_private: bool) -> list[Target]:
            # A dependency's *private* link_libs do not propagate to consumers,
            # so we only follow public ones when recursing.
            deps = list(target._dependencies)
            deps += [t for t in target.public.link_libs if isinstance(t, Target)]
            if include_private:
                deps += [t for t in target.private.link_libs if isinstance(t, Target)]
            return deps

        def _collect(target: Target, *, include_private: bool) -> None:
            for dep in direct_deps(target, include_private=include_private):
                if dep.name not in visited:
                    visited.add(dep.name)
                    _collect(dep, include_private=False)
                    result.append(dep)

        _collect(self, include_private=True)
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
            lines.append(
                f"  Dependencies: {[d.qualified_name for d in self.dependencies]}"
            )
        if self.public.include_dirs:
            lines.append(f"  Public includes: {self.public.include_dirs}")
        if self.public.defines:
            lines.append(f"  Public defines: {self.public.defines}")
        return "\n".join(lines)

    def __repr__(self) -> str:
        deps = ", ".join(d.qualified_name for d in self.dependencies)
        return f"Target({self.qualified_name!r}, deps=[{deps}])"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Target):
            return NotImplemented
        return self.qualified_name == other.qualified_name

    def __hash__(self) -> int:
        return hash(self.qualified_name)


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
