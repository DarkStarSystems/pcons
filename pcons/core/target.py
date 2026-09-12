# SPDX-License-Identifier: MIT
"""Target abstraction with usage requirements.

A Target represents something that can be built (a library, program, etc.)
and carries "usage requirements" that propagate to consumers (CMake-style).
"""

from __future__ import annotations

import logging
from collections import UserList
from collections.abc import Callable, Iterable, MutableSequence, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeAlias, TypeVar

from pcons.core.flags import merge_flags
from pcons.core.names import validate_name
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


#: Usage-requirement names that mean something to pcons. Anything else is a
#: typo until a toolchain declares otherwise: the lists are consumed by name,
#: so `target.private.lib_dirs.append(...)` would be accepted, stored, and
#: never looked at again — the link then fails naming the library rather than
#: the mistake. Extension authors add their own with
#: :func:`register_usage_requirement`.
#:
#: The type annotations for these live in pcons/_gen_stubs.py; a test keeps
#: the two lists in step.
_KNOWN_USAGE_REQUIREMENTS: set[str] = {
    "include_dirs",
    "system_include_dirs",
    "defines",
    "compile_flags",
    "link_flags",
    "link_libs",
    "link_dirs",
    "frameworks",
    "framework_dirs",
}


def _unknown_requirement_message(name: str) -> str:
    """Explain an unrecognized usage-requirement name, and guess the intent."""
    import difflib

    known = sorted(_KNOWN_USAGE_REQUIREMENTS)
    message = (
        f"Unknown usage requirement '{name}'. Nothing reads it, so setting it "
        f"would have no effect on the build."
    )
    close = difflib.get_close_matches(name, known, n=1, cutoff=0.6)
    if close:
        message += f" Did you mean '{close[0]}'?"
    message += (
        f"\n  Known names: {', '.join(known)}."
        f"\n  A toolchain or extension that consumes its own name declares it "
        f"with pcons.core.target.register_usage_requirement()."
    )
    return message


def register_usage_requirement(name: str) -> None:
    """Declare a usage-requirement name a toolchain or extension consumes.

    Only needed for names pcons doesn't already know
    (:data:`_KNOWN_USAGE_REQUIREMENTS`); registering one makes
    ``target.public.<name>`` and ``target.private.<name>`` usable instead of
    raising. Registration is process-wide and idempotent.
    """
    _KNOWN_USAGE_REQUIREMENTS.add(name)


def known_usage_requirements() -> frozenset[str]:
    """Every usage-requirement name currently recognized."""
    return frozenset(_KNOWN_USAGE_REQUIREMENTS)


# Target options (target.set_option()), name → what consumes it. Core defines
# none: an option means something only to the builder or toolchain that reads
# it, and that's what declares it here.
_KNOWN_TARGET_OPTIONS: dict[str, str] = {}


def register_target_option(name: str, description: str) -> None:
    """Declare a target option a builder or toolchain consumes.

    Makes ``target.set_option(name, ...)`` legal; an undeclared name raises,
    since nothing would read it. The description is shown when a set_option()
    call misses. Registration is process-wide and idempotent.
    """
    _KNOWN_TARGET_OPTIONS[name] = description


def known_target_options() -> dict[str, str]:
    """Every target option currently declared, name → description."""
    return dict(_KNOWN_TARGET_OPTIONS)


def _unknown_option_message(key: str) -> str:
    """Explain an unrecognized target option, and guess the intent."""
    import difflib

    known = sorted(_KNOWN_TARGET_OPTIONS)
    message = (
        f"Unknown target option '{key}'. No builder or toolchain reads it, so "
        f"setting it would have no effect on the build."
    )
    close = difflib.get_close_matches(key, known, n=1, cutoff=0.6)
    if close:
        message += f" Did you mean '{close[0]}'?"
    if known:
        message += "\n  Known options:\n" + "\n".join(
            f"    {name}: {_KNOWN_TARGET_OPTIONS[name]}" for name in known
        )
    else:
        message += "\n  No options are declared."
    return message + (
        "\n  A builder or toolchain that reads its own option declares it with "
        "pcons.core.target.register_target_option()."
    )


class UsageRequirements(_UsageRequirementsStubs):
    """Requirements that propagate from a target to its consumers (CMake-style):
    when A depends on B, B's public usage requirements are added to A's build.

    Stores named lists of values via attribute access. C/C++ toolchains use
    include_dirs, defines, compile_flags, link_flags, link_libs, link_dirs;
    a toolchain can add its own with :func:`register_usage_requirement`.
    An unrecognized name raises rather than quietly becoming a list nothing
    reads.

    The ``link_libs`` list is special: appending a ``Target`` creates a full
    dependency (the owner inherits that target's public usage requirements —
    headers, defines, transitive link libs — and links its output), while
    appending a ``str`` adds only a raw link token (``"m"`` → ``-lm``) with
    no usage requirements. The ``public`` scope re-exports to consumers;
    ``private`` does not. ``target.link(...)`` and ``target.link_private(...)``
    are the recommended high-level equivalents.

    A field may use a special list type (``UniqueList`` dedup, or
    ``ValidatedUniqueList`` whose ``on_append`` hook enforces invariants and
    invalidates caches). Whole-list assignment preserves those semantics:
    ``__setattr__`` replaces an existing list's *contents* in place, so
    ``reqs.link_libs = [a, b]`` behaves like repeated ``.append()``.
    """

    _data: dict[str, list[Any] | UserList[Any]]

    def __init__(self, **kwargs: list[Any] | UserList[Any]) -> None:
        object.__setattr__(self, "_data", {})
        for k, v in kwargs.items():
            self._data[k] = v

    def __getattr__(self, name: str) -> list[Any] | UserList[Any]:
        """Return the named list, creating it on first access.

        Raises AttributeError for an unrecognized name — which also keeps
        dunder probes (``copy``, ``pickle``) from being answered with an
        empty list.
        """
        if name not in _KNOWN_USAGE_REQUIREMENTS:
            raise AttributeError(_unknown_requirement_message(name))
        data: dict[str, list[Any] | UserList[Any]] = object.__getattribute__(
            self, "_data"
        )
        return data.setdefault(name, [])

    def __setattr__(self, name: str, value: list[Any] | UserList[Any]) -> None:  # type: ignore[override]
        if name.startswith("_"):
            object.__setattr__(self, name, value)
        else:
            if name not in _KNOWN_USAGE_REQUIREMENTS:
                raise AttributeError(_unknown_requirement_message(name))
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
                items = list(value)
                existing.clear()
                existing.extend(items)
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

    def make_includes_system(self) -> None:
        """Move every include directory to ``system_include_dirs``, in place.

        Headers that are not ours to fix should not be held to our warning
        set. Idempotent, and safe to call after the lists are populated, which
        is what makes it usable on a target someone else created::

            vendored.public.make_includes_system()

        Prefer the ``system=`` argument of ``find_package()`` / ``env.use()``
        when the requirements come from a package.
        """
        include_dirs = self._data.get("include_dirs")
        if not include_dirs:
            return
        moved = list(include_dirs)
        include_dirs.clear()
        system = self.system_include_dirs
        for inc in moved:
            if inc not in system:
                system.append(inc)

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


def _validate_target_name(name: str) -> None:
    """Raise ValueError unless the target name is well-formed."""
    validate_name("Target", name)


def split_qualified_name(
    name: str, raise_on_invalid: bool = True
) -> tuple[str | None, str]:
    """Split a qualified "project::target" name into (project, target);
    an unqualified name returns (None, name).
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


def split_target_spec(spec: str) -> tuple[str | None, str, str | None]:
    """Split ``"project::target@env"`` into (project, target, env).

    ``@`` binds tighter than ``::``, so ``"sub::common@mcu"`` is target
    ``common`` of project ``sub``, built in environment ``mcu``. Missing parts
    come back as None.

    Raises:
        ValueError: On more than one ``@``, or an empty environment name.
    """
    project, target = split_qualified_name(spec)
    parts = target.split("@")
    if len(parts) == 1:
        return project, target, None
    if len(parts) > 2:
        raise ValueError(f"Invalid target spec: {spec!r}. Too many '@' separators.")
    name, env = parts
    if not env:
        raise ValueError(
            f"Invalid target spec: {spec!r}. Name the environment after the '@'."
        )
    return project, name, env


def is_qualified_name(name: str) -> bool:
    """Check if a name has a project qualifier ("project::target")."""
    project, _target = split_qualified_name(name, raise_on_invalid=False)
    return project is not None


def _make_default_requirements(
    link_libs_validator: Callable[[Any], None],
) -> UsageRequirements:
    """Create a default UsageRequirements with standard C/C++ fields."""
    reqs = UsageRequirements()
    reqs.defines = UniqueList([])
    reqs.include_dirs = UniqueList([])
    reqs.system_include_dirs = UniqueList([])
    reqs.link_dirs = UniqueList([])
    # compile_flags/link_flags are plain lists, NOT UniqueList: token-level
    # dedup corrupts paired flags (-framework Foo -framework Bar, -Xlinker, -arch,
    # repeated -L/-F). Flag dedup is pair-aware and happens via merge_flags() when
    # usage requirements are merged. Direct appends are preserved verbatim.
    reqs.compile_flags = []
    reqs.link_flags = []
    # link_libs semantics (Target vs str, public vs private): see the
    # UsageRequirements docstring.
    reqs.link_libs = ValidatedUniqueList([], on_append=link_libs_validator)
    return reqs


def _looks_like_a_path(name: str) -> bool:
    """Whether a link() string is a file path rather than a library name.

    A name is what ``-l`` takes: no directory separators, no library file
    suffix. ``/opt/vendor/lib/libfoo.a`` fails at link time as
    ``-l/opt/vendor/lib/libfoo.a``, with the mistake pointed at the linker.
    """
    return (
        "/" in name
        or "\\" in name
        or name.endswith((".a", ".lib", ".so", ".dylib", ".dll", ".o", ".obj"))
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
        intermediate_nodes: Intermediate build artifacts (e.g., object files).
        output_nodes: Final output nodes (populated by resolver).
    """

    __slots__ = (
        "name",
        "builder",
        "_sources",
        "_dependencies",
        "_on_change",
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
        # Per-source environment overrides (add_sources(..., env=...)),
        # keyed by source node path.
        "_source_envs",
        # Scanners attached via Scanner.attach() (pcons/core/scan.py).
        "_scanners",
        # Membership index for _sources, so duplicate detection stays O(1)
        # on targets with thousands of sources.
        "_source_set",
        "_subdir",
        # Utility targets (lupdate, doc generation, ...) set this False:
        # excluded from 'all' and implicit defaults, built only when
        # requested by name/alias or listed in Default().
        "build_by_default",
    )

    def __init__(
        self,
        name: str,
        *,
        target_type: str | None = None,
        builder: Builder | None = None,
        defined_at: SourceLocation | None = None,
        project: Project | None = None,
        env: Environment | None = None,
    ) -> None:
        """Create a target. Toolchains define their own target_type strings.

        ``project`` is the owning project; builder factories pass the project
        they were reached through. Without it, the most recently created
        project owns the target (fine in a single-project script).

        ``env`` is the environment the target builds in. A builder that has one
        passes it here rather than assigning it afterwards: a target's name and
        environment are its identity, and the project checks that identity as
        soon as the target is registered, which happens before ``__init__``
        returns.
        """
        _validate_target_name(name)
        self.name = name
        self.builder = builder
        self._sources: list[Node] = []
        self._source_set: set[Node] = set()
        # Every depends() edge: a target to build first, or a file to have.
        self._dependencies: list[Target | Node] = []
        # depends(on_change=...) choices; a dependency absent here lets
        # each step decide.
        self._on_change: dict[Target | Node, bool] = {}
        self.public = _make_default_requirements(self.__link_libs_validator)
        self.private = _make_default_requirements(self.__link_libs_validator)
        self.required_languages: set[str] = set()
        self.defined_at = defined_at or get_caller_location()
        self._collected_requirements: UsageRequirements | None = None
        self.target_type: str | None = target_type
        self._env: Environment | None = env
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
        # Sources resolved after the main resolve phase (for Install, etc.)
        self._pending_sources: list[Target | Node | Path | str] | None = None
        self.build_by_default: bool = True  # see __slots__ comment
        # Build info for archive and command targets
        self._build_info: BuildInfo | dict[str, Any] | None = None
        self._builder_name: str | None = None
        self._builder_data: dict[str, Any] = {}
        # Sources that compile with an environment other than the target's
        # (add_sources(..., env=...)), keyed by source node path.
        self._source_envs: dict[Path, Environment] = {}
        # Scanners attached via Scanner.attach(target); wired by the
        # resolver's ScannerResolver pass (see pcons/core/scan.py).
        self._scanners: list[Any] = []

        if project is None:
            from pcons.core.project import Project

            project = Project.current()

        self.__project = project
        # Where this target's paths sit relative to the top-level root, which
        # is the form node paths are stored in.
        self._subdir = project._node_offset

        if self._env is None:
            self._env = project._inherited_environment()

        self.__project._add_target(self)

    @property
    def project(self) -> Project:
        """Get the project this target belongs to."""
        return self.__project

    @property
    def qualified_name(self) -> str:
        """The full spelling, ``project::target@env``.

        The ``@env`` half is there when the environment is named. An unnamed
        environment cannot host a duplicate name and cannot be addressed, so
        there is nothing to say about it.
        """
        env = self._env
        env_name = env.name if env is not None else None
        suffix = f"@{env_name}" if env_name else ""
        return f"{self.project.name}::{self.name}{suffix}"

    @property
    def env(self) -> Environment | None:
        """The environment this target builds in."""
        return self._env

    @property
    def dependencies(self) -> tuple[Target, ...]:
        """Every target that must be resolved before this one: the targets
        it depends on, the targets whose outputs are its sources, and the
        libraries it links."""
        pending = self._pending_sources or ()
        return tuple(
            dict.fromkeys(
                (
                    *self._dependency_targets(),
                    *(t for t in pending if isinstance(t, Target)),
                    *(t for t in self.public.link_libs if isinstance(t, Target)),
                    *(t for t in self.private.link_libs if isinstance(t, Target)),
                )
            )
        )

    def _dependency_targets(self) -> list[Target]:
        """The targets named by depends(), in order."""
        return [d for d in self._dependencies if isinstance(d, Target)]

    @property
    def sources(self) -> list[Node]:
        """Source nodes for this target (a new list; use add_source(s) to modify).

        Target sources from _pending_sources appear only after those Targets
        have been resolved (output_nodes populated).
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
        """Always raises; use add_source() or add_sources()."""
        raise AttributeError(
            f"Cannot assign directly to {self.name}.sources. "
            f"Use add_source() or add_sources() instead. "
            f"Example: target.add_sources({value!r})"
        )

    @property
    def build_dir(self) -> Path:
        """This target's build directory.

        The environment places it: it holds the directory it was given and its
        ``build_prefix``. ``_subdir`` is the offset from the top-level root, so
        this anchors there rather than at the owning project's own directories,
        which already include that offset.
        """
        env = self._env
        if env is not None:
            return env.build_dir_for(self._subdir)
        top = self.__project.top
        return top.build_dir / self._subdir if self._subdir.parts else top.build_dir

    @property
    def source_dir(self) -> Path:
        """This target's source directory."""
        top = self.__project.top
        return top.root_dir / self._subdir if self._subdir.parts else top.root_dir

    @property
    def path_resolver(self) -> PathResolver:
        """Get the Path resolver for this target's directory."""
        resolver = self.project._path_resolver  # top-anchored
        return resolver.subdir(self._subdir) if self._subdir.parts else resolver

    @property
    def nodes(self) -> list[FileNode]:
        """All build nodes for this target (intermediate + output)."""
        return self.intermediate_nodes + self.output_nodes

    def __link_libs_validator(self, item: Target | str):
        if self._resolved:
            raise RuntimeError(f"Cannot modify target '{self.name}' after resolve(). ")
        if item is self:
            raise ValueError(f"Target '{self.name}' cannot link itself.")
        if isinstance(item, Target):
            self._check_same_tree(item, "link")
        # Invalidate cached requirements
        self._collected_requirements = None

    def _check_same_tree(self, other: Target, verb: str) -> None:
        """Refuse an edge to a target in another top-level project.

        Sibling projects build independently, with separate build directories and
        separate build files, so raise an error if user tries to connect them.
        Imported targets are exempt: they
        describe something outside every build.
        """
        if getattr(other, "is_imported", False):
            return
        if other.project.top is self.project.top:
            return
        from pcons.core.errors import PconsError

        raise PconsError(
            f"target '{self.name}' (project "
            f"{self.project.top.name!r}) cannot {verb} '{other.name}' from "
            f"project {other.project.top.name!r}: sibling projects build "
            "independently, with no edges between their build files.\n"
            "Build it in this project too (e.g. add_subdirectory() the "
            "same directory from both), or make the two one project."
        )

    def link(self, *libs: Target | str) -> Target:
        """Add PUBLIC link dependencies (fluent API).

        Appends each argument to ``self.public.link_libs``: a ``Target``
        becomes a full dependency (its public headers, defines, flags, and
        transitive link libs propagate here), a ``str`` is a raw link token;
        see :class:`UsageRequirements`. To link a target by name, look it up:
        ``link(project.get_target("sub::common@mcu"))``. Public dependencies
        are re-exported
        to this target's consumers, like CMake's ``target_link_libraries(...
        PUBLIC ...)``. Duplicates are ignored; argument order is preserved
        (link order can matter for static libraries).

        Args:
            *libs: Targets to depend on, and/or raw library-name strings.

        Returns:
            self, for method chaining.

        Raises:
            TypeError: If an argument is not a Target or str. Pass lists
                unpacked: ``target.link(*libs)``, not ``target.link(libs)``.
            ValueError: If a target links itself, or a library name is empty.
            RuntimeError: If called after the target has been resolved.

        Example:
            libmath = project.StaticLibrary("math", env, sources=["math.c"])
            libmath.public.include_dirs.append("include")
            libmath.link("m")  # consumers of libmath also get -lm

            libphysics = project.SharedLibrary("physics", env, sources=["physics.c"])
            libphysics.link(libmath)  # re-exports libmath (headers and all)

            app = project.Program("app", env, sources=["main.c"])
            app.link_private(libphysics)  # app gets physics + math + -lm
        """
        return self._link_into(self.public.link_libs, libs, "link")

    def link_private(self, *libs: Target | str) -> Target:
        """Add PRIVATE link dependencies (fluent API).

        Like :meth:`link`, but appends to ``self.private.link_libs``: the
        dependencies still apply to *this* target's build but are NOT
        re-exported to its consumers (CMake's ``target_link_libraries(...
        PRIVATE ...)``). The right choice for implementation details and for
        programs, which have no consumers.

        Note: a private dependency of a *static* library still reaches the
        final program's link line (an archive does not contain its
        dependencies), without propagating headers or defines.

        Example:
            core = project.StaticLibrary("core", env, sources=["core.c"])
            core.link_private(zlib)  # core uses zlib; consumers never see zlib headers

            app = project.Program("app", env, sources=["main.c"])
            app.link_private(core, "pthread")
        """
        return self._link_into(self.private.link_libs, libs, "link_private")

    def _link_into(
        self,
        link_libs: MutableSequence[Target | str],
        libs: tuple[Target | str, ...],
        method: str,
    ) -> Target:
        """Validate and append link dependencies; shared by link()/link_private()."""
        if self._resolved:
            raise RuntimeError(
                f"Cannot modify target '{self.name}' after resolve(). "
                f"Add link dependencies before project.resolve() or project.generate()."
            )
        for lib in libs:
            if isinstance(lib, (list, tuple)):
                raise TypeError(
                    f"{method}() takes individual arguments, not a list. "
                    f"Use target.{method}(a, b) or target.{method}(*libs)."
                )
            if not isinstance(lib, (Target, str)):
                raise TypeError(
                    f"{method}() requires Target objects or library-name strings, "
                    f"got {type(lib).__name__}. Pass a Target to depend on it "
                    f"(bringing its headers and usage requirements), or a string "
                    f"like 'm' for a raw system library."
                )
            if isinstance(lib, str) and not lib.strip():
                raise ValueError(f"{method}() got an empty library name.")
            if isinstance(lib, str) and _looks_like_a_path(lib):
                raise TypeError(
                    f"{method}() got {lib!r}, which looks like a file path; a "
                    f"string here is a library name, passed to the linker as "
                    f"-l{lib}. To link a library file by path, put its "
                    f"directory in link.libdirs and name it, or add the file "
                    f"itself to link_flags as a PathToken."
                )
            if lib is self:
                raise ValueError(f"Target '{self.name}' cannot link itself.")
            link_libs.append(
                lib
            )  # ValidatedUniqueList: validates, de-dupes, invalidates cache
        return self

    def depends(
        self, *items: Target | Node | Path | str, on_change: bool | None = None
    ) -> Target:
        """Build *items* before this target (fluent API).

        A target is built first, and its public usage requirements
        (headers, defines, flags) apply here and to this target's consumers,
        as with ``link()`` -- but it is not linked. A file (Node, Path or
        str, read from the directory of the script that declared this
        target, like ``add_sources()``) is up to date first, without being
        passed as a source.

        Each build step of this target then holds the dependency's outputs
        (or the file) as tightly as it needs to. A step that records what it
        reads -- a compile with a depfile -- waits only for them to exist,
        and its own record says whether a change reruns it. A step that
        records nothing reruns whenever they change. So ``app.depends(
        "app.ld")`` relinks when the linker script changes and leaves the
        compiles alone, and ``lib.depends(gen)`` recompiles only the sources
        that included what ``gen`` wrote. What a library you ``link()``
        waits for reaches your compiles the same way, so a library whose
        public headers are generated declares the generator once.

        ``on_change`` overrides that choice for every step. ``True`` is for
        a file a step reads but cannot report -- a response file, a
        sanitizer ignore-list, an options file the tool never names in its
        depfile: the step reruns when it changes. ``False`` is for
        something a step only needs to exist: it never reruns the step by
        itself.

        Args:
            items: Targets, or files as Node, Path or str.
            on_change: ``True``, rerun every step when an item changes;
                ``False``, only build the items first; ``None`` (default),
                each step decides as described above.

        Returns:
            self for method chaining.

        Raises:
            ValueError: If a target depends on itself.
            RuntimeError: If called after the target has been resolved.

        Example:
            gen = env.Command(
                target="generated.h",
                source="schema.json",
                command="python codegen.py $SOURCE -o $TARGET",
                restat=True,
            )
            # Generated header: depends() so compile steps wait for it.
            app = project.Program("app", env, sources=["main.c"])
            app.depends(gen)
        """
        from pcons.core.node import Node

        if self._resolved:
            raise RuntimeError(
                f"Cannot modify target '{self.name}' after resolve(). "
                f"Add dependencies before project.resolve() or project.generate()."
            )
        for item in items:
            if isinstance(item, Target):
                if item is self:
                    raise ValueError(f"Target '{self.name}' cannot depend on itself.")
                self._check_same_tree(item, "depend on")
            elif not isinstance(item, Node):
                # str or Path: a FileNode via the project
                if self._subdir.parts:
                    item = self._subdir / item
                item = self.project.node(item)
            if item not in self._dependencies:
                self._dependencies.append(item)
            if on_change is not None:
                self._on_change[item] = on_change
        # Invalidate cached requirements
        self._collected_requirements = None

        return self

    def _apply_dependencies(self) -> None:
        """Wire the depends() edges onto this target's nodes.

        Called by the resolver once this target's nodes exist and its
        dependencies are resolved; see :meth:`depends` and
        :meth:`FileNode.wait_for`.
        """
        nodes = self.intermediate_nodes + self.output_nodes
        for dep in self._dependencies:
            outputs = self._waited_outputs(dep)
            on_change = self._on_change.get(dep)
            for node in nodes:
                if on_change is None:
                    node.wait_for(outputs)
                elif on_change:
                    node.depends(outputs)
                else:
                    node.order_after(outputs)

    def _waited_outputs(self, dep: Target | Node) -> list[Node]:
        """What this target's steps wait for on account of *dep*: its
        outputs, less any that are this target's own nodes.

        An ObjectLibrary given as a source is a dependency whose outputs
        are adopted as this target's own objects. Each is built by the
        ObjectLibrary's edge; a compile here waiting for a sibling object
        is at best idle and at worst a cycle, through whatever generated
        that sibling's source, and waiting for itself is a cycle outright.
        """
        outputs = dep.ordering_outputs() if isinstance(dep, Target) else [dep]
        own = {id(n) for n in (*self.intermediate_nodes, *self.output_nodes)}
        return [out for out in outputs if id(out) not in own]

    def ordering_outputs(self, seen: set[Target | Node] | None = None) -> list[Node]:
        """What a dependent of this target waits for.

        This target's outputs -- or, for a target that builds nothing of its
        own (an interface library), whatever its own dependencies produce,
        since that ordering can only hold in the dependent. ``seen`` keeps a
        chain of such targets finite even before cycle detection has run.
        """
        if seen is None:
            seen = set()
        if self.output_nodes:
            return list(self.output_nodes)
        result: list[Node] = []
        for dep in self._dependencies:
            if dep in seen:
                continue
            seen.add(dep)
            if isinstance(dep, Target):
                result.extend(dep.ordering_outputs(seen))
            else:
                result.append(dep)
        return result

    def inherited_dependency_outputs(self) -> list[Node]:
        """What the targets this one links wait for, for this target's own
        build steps to wait for too.

        A linked library's public headers are on this target's compile line,
        but only the link step waits for the library itself, so the compiles
        would race whatever fills those headers. A library whose public
        headers are generated therefore declares ``lib.depends(gen)`` once,
        and every target that links it waits for the same generator: this
        returns the outputs of every dependency (a ``depends()``, a generated
        source) of every target in the link closure.

        A target reached through ``depends()`` needs none of this: this
        target's steps wait for its outputs directly, and those wait for
        everything it needs.
        """
        seen: set[int] = set()
        result: list[Node] = []
        for member in self.transitive_link_dependencies():
            for dep in member._dependencies:
                if dep is self:
                    continue
                for node in member._waited_outputs(dep):
                    if id(node) not in seen:
                        seen.add(id(node))
                        result.append(node)
        return result

    def add_source(
        self, source: Target | Node | Path | str, *, env: Environment | None = None
    ) -> Target:
        """Add a source to this target (fluent API).

        A Target source's output files become sources after that Target is
        resolved.

        Args:
            source: The source to add.
            env: Compile this one source with a different environment (see
                :meth:`add_sources`).

        Example:
            generated = env.Command(target="gen.cpp", source="gen.y", command="...")
            program.add_source(generated)
        """
        if isinstance(source, Target):
            self._add_source_target(source)
        else:
            self._add_source_node(self._to_node(source), env)
        return self

    def add_sources(
        self,
        sources: Sequence[Target | Node | Path | str],
        *,
        base: Path | str | None = None,
        env: Environment | None = None,
    ) -> Target:
        """Add multiple sources to this target (fluent API).

        Args:
            sources: Source files (Targets, Nodes, Paths, or string paths).
                A Target's output files become sources after it is resolved.
            base: Optional base directory for relative Path/string sources.
            env: Compile these sources with a different environment than the
                rest of the target's. The target's own usage requirements
                (private/public include dirs, defines, and those inherited
                from dependencies) still apply — only the environment layer
                changes, which is what per-file flag tweaks want.

                On a source the target already has, ``env`` sets that source's
                environment rather than adding a second copy, so the file need
                not be held out of the main source list.

        Returns:
            self for method chaining.

        Raises:
            ValueError: A source is already in this target and no ``env`` is
                given, so re-adding it could only duplicate it in the link.

        Example:
            lib = project.StaticLibrary("core", env, sources=common_sources)

            # One file that miscompiles at -O2 (already in common_sources)
            with env.override() as careful:
                careful.cxx.flags.append("-O1")
                lib.add_sources(["cuda-support.cxx"], env=careful)
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
                self._add_source_target(source)
            else:
                if base_path and isinstance(source, (str, Path)):
                    path = Path(source)
                    if not path.is_absolute():
                        source = base_path / path
                # Only join subdir when source is a string or Path. If it's
                # already a Node, leave it alone.
                if self._subdir and isinstance(source, (str, Path)):
                    source = Path(self._subdir) / source
                self._add_source_node(self._to_node(source), env)
        return self

    def _add_source_target(self, source: Target) -> None:
        """Add a Target source, whose outputs become sources once resolved."""
        if self._pending_sources is None:
            self._pending_sources = []
        if source in self._pending_sources:
            raise ValueError(
                f"Target '{self.name}' already has source target "
                f"'{source.name}'. Its outputs would be built once and "
                f"consumed twice."
            )
        self._pending_sources.append(source)
        # Any step of a compiled target may read any of a source target's
        # outputs (a header beside a generated source), so the whole target
        # waits for it, as for a depends() target.
        self.depends(source)

    def _add_pending_sources(
        self, sources: Sequence[Target | Node | Path | str]
    ) -> None:
        """Sources a factory turns into inputs once their targets resolve
        (Install, Tarfile, env.Command). Each step consumes the inputs it
        lists and nothing else, so a source target orders this target's
        resolution but is not a dependency of every step."""
        self._pending_sources = list(sources)

    def _add_source_node(self, node: Node, env: Environment | None) -> None:
        """Add one source node, rejecting a source the target already has.

        A duplicate compiles once (object nodes are shared) but is consumed
        twice, so the linker reports duplicate symbols against a single object
        file — a build-description mistake that reads like a linker bug. With
        an ``env``, though, re-naming a source is the natural way to say "this
        one file compiles differently", so that sets its environment in place.
        """
        if node in self._source_set:
            if env is None:
                raise ValueError(
                    f"Target '{self.name}' already has source '{node.name}'. "
                    f"Adding it again links it twice, which the linker reports "
                    f"as duplicate symbols in one object file.\n"
                    f"  To compile this one file differently, pass env= — on a "
                    f"source the target already has, that sets the source's "
                    f"environment instead of adding a copy."
                )
            self._set_source_env(node, env)
            return
        self._source_set.add(node)
        self._sources.append(node)
        if env is not None:
            self._set_source_env(node, env)

    def _set_source_env(self, node: Node, env: Environment) -> None:
        """Record the environment one source compiles with.

        Two different environments for the same source in one target would
        make the object file ambiguous — both would write the same path — so
        that's an error rather than a last-one-wins surprise.
        """
        from pcons.core.node import FileNode as _FileNode

        if not isinstance(node, _FileNode):
            return
        existing = self._source_envs.get(node.path)
        if existing is not None and existing is not env:
            raise ValueError(
                f"Source '{node.path}' was added to target '{self.name}' twice "
                f"with different environments. One source compiles once per "
                f"target; use a separate target if you need two variants."
            )
        self._source_envs[node.path] = env

    def _to_node(self, source: Node | Path | str) -> Node:
        """Convert a source specification to a Node."""
        from pcons.core.node import Node as NodeClass

        if isinstance(source, NodeClass):
            return source
        path = Path(source)
        return self.project.node(path)

    def set_option(self, key: str, value: Any) -> Target:
        """Set a builder/toolchain option on this target (fluent API).

        The core does not interpret these values — their meaning is defined
        by the builder or toolchain that declared the option with
        :func:`register_target_option`. Returns self for chaining.

        Raises:
            ValueError: If no builder or toolchain declared ``key``, so
                nothing would read it.
        """
        if key not in _KNOWN_TARGET_OPTIONS:
            raise ValueError(_unknown_option_message(key))
        self._builder_data[key] = value
        return self

    def get_option(self, key: str, default: Any = None) -> Any:
        """Get an option previously set with :meth:`set_option`."""
        if key not in _KNOWN_TARGET_OPTIONS:
            raise ValueError(_unknown_option_message(key))
        return self._builder_data.get(key, default)

    def pre_build(self, command: str) -> Target:
        """Add a shell command to run before the target is built (fluent API).

        The mirror of :meth:`post_build`, with the same ``$out``/``$in``
        substitutions. Commands run in the order added, ahead of the target's
        own command.

        Example:
            gen.pre_build("python -m pcons.tools.stable_output --pre $out")
        """
        if "pre_build_commands" not in self._builder_data:
            self._builder_data["pre_build_commands"] = []
        self._builder_data["pre_build_commands"].append(command)
        return self

    def post_build(self, command: str) -> Target:
        """Add a shell command to run after the target is built (fluent API).

        Commands run in the order added and support $out (primary output
        path) and $in (input files, space-separated).

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
        """Return this target's private requirements plus all public
        requirements from the dependency tree (cached)."""
        if self._collected_requirements is not None:
            return self._collected_requirements

        result = self.private.clone()
        visited: set[str] = set()
        self._collect_from_deps(result, visited)

        self._collected_requirements = result
        return result

    def _collect_from_deps(self, result: UsageRequirements, visited: set[str]) -> None:
        """Merge public requirements from all transitive dependencies.

        ``transitive_dependencies()`` already returns the full public-edge
        closure (a private dep of a dependency is not re-exported), so we
        simply merge each one's public requirements. We must NOT recurse via
        ``dep._collect_from_deps`` here: that re-enters each dependency at its
        own top level, where its *private* link_libs are followed, which would
        leak private dependencies' headers up to consumers.
        """
        for dep in self.transitive_dependencies():
            if dep.qualified_name in visited:
                continue
            visited.add(dep.qualified_name)
            result.merge(dep.public)

    def get_all_languages(self) -> set[str]:
        """All languages required by this target and its dependencies
        (e.g. {'c', 'cxx'}); used to pick the linker."""
        languages = set(self.required_languages)
        visited: set[str] = {self.qualified_name}

        for dep in self.dependencies:
            if dep.qualified_name not in visited:
                visited.add(dep.qualified_name)
                languages.update(dep.get_all_languages())

        return languages

    def transitive_dependencies(self) -> list[Target]:
        """Every target whose public usage requirements reach this one (DFS
        order, no duplicates, not including self): the targets it depends on
        and links, and theirs, through public edges. A dependency's private
        link_libs stay with it."""
        return self._closure(for_link=False)

    def transitive_link_dependencies(self) -> list[Target]:
        """Every target whose output is a link input of this one (DFS order,
        no duplicates, not including self): the linked targets, with private
        link_libs followed through static libraries, since an archive does
        not contain its dependencies. depends() targets are not linked."""
        return self._closure(for_link=True)

    def _closure(self, *, for_link: bool) -> list[Target]:
        result: list[Target] = []
        visited: set[str] = set()

        def direct_deps(target: Target, *, include_private: bool) -> list[Target]:
            # A dependency's *private* link_libs do not propagate to consumers,
            # so we only follow public ones when recursing.
            deps = [] if for_link else target._dependency_targets()
            deps += [t for t in target.public.link_libs if isinstance(t, Target)]
            if include_private:
                deps += [t for t in target.private.link_libs if isinstance(t, Target)]
            return deps

        def _collect(target: Target, *, include_private: bool) -> None:
            for dep in direct_deps(target, include_private=include_private):
                if dep.qualified_name not in visited:
                    visited.add(dep.qualified_name)
                    # A static library's private link deps are not baked into the
                    # archive, so they must follow through to the link line. Shared
                    # libraries and other targets resolve their own private deps.
                    recurse_private = for_link and dep.target_type == "static_library"
                    _collect(dep, include_private=recurse_private)
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
        super().__init__(name, defined_at=defined_at)
        self.is_imported = True
        self.package_name = package_name or name
        self.version = version

    def __repr__(self) -> str:
        version = f" v{self.version}" if self.version else ""
        return f"ImportedTarget({self.name!r}{version})"
