# SPDX-License-Identifier: MIT
"""Node hierarchy for the pcons dependency graph.

Nodes are the fundamental unit in the dependency graph. Each node represents
something that can be a dependency or a target in the build system.

Node types:
    - FileNode: A file (source or generated)
    - DirNode: A directory with special semantics for targets vs sources
    - ValueNode: A computed value (e.g., config hash, version string)
    - AliasNode: A named group of targets (phony target)
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, TypedDict

from pcons.util.source_location import SourceLocation, get_caller_location

if TYPE_CHECKING:
    from pcons.core.builder import Builder
    from pcons.core.subst import PathToken
    from pcons.core.target import Target
    from pcons.tools.toolchain import ToolchainContext

logger = logging.getLogger(__name__)


class OutputInfo(TypedDict, total=False):
    """Information about a single output in a multi-output build.

    ``implicit`` outputs are not tracked by Ninja; ``required`` outputs
    must be generated.
    """

    path: Path
    suffix: str
    implicit: bool
    required: bool


class BuildInfo(TypedDict, total=False):
    """Build information stored in a node's ``_build_info`` for code generation.

    Different builders populate different subsets of these fields.
    """

    # Common fields
    tool: str  # Tool name (e.g. "cc", "link", "copy")
    command_var: str  # Variable holding the command template (e.g. "objcmd")
    language: str | None  # Language for linker selection (e.g. "c", "cxx")
    sources: list[Any]  # list[Node], but avoid circular import
    depfile: PathToken | None  # PathToken with suffix for depfile path
    deps_style: str | None  # Ninja dependency style ("gcc" or "msvc")
    command: str | list[str]  # Direct command (generic/custom builders)
    description: str  # Human-readable build description

    # Toolchain-provided context; the resolver uses
    # context.get_env_overrides() when expanding command templates
    context: ToolchainContext | None

    # Multi-output builds
    outputs: dict[str, OutputInfo]
    all_output_nodes: dict[str, Any]  # dict[str, FileNode]
    primary_node: Any  # FileNode (set on secondary outputs)
    output_name: str  # This output's name (set on secondary outputs)

    # Generic command builder
    rule_name: str  # Custom Ninja rule name
    all_targets: list[Any]  # list[Node]
    restat: bool  # Ninja restat: re-check output timestamp after build

    # Per-build variables for standalone tools (Install, Archive)
    # These are written as Ninja build-level variables
    variables: dict[str, str]

    # Ninja dyndep file path (relative to build dir) for build statements
    # whose dynamic dependencies live in an external dyndep file. Used by
    # the C++ module and Fortran module scanners.
    dyndep: str

    # Extra command-line flags appended verbatim to the build command, after
    # template expansion. Used by the GCC C++ module path to attach
    # -fmodule-mapper=/-Mno-modules to specific object compiles.
    extra_command_flags: list[str]

    # Environment reference for command expansion
    # Used by resolver to expand command templates
    env: Any  # Environment, but avoid circular import


class Node(ABC):
    """Abstract base class for all nodes in the dependency graph.

    A Node represents something that can be a dependency or target.
    Nodes track their dependencies (both explicit and implicit) and
    where they were defined for debugging.

    Attributes:
        explicit_deps: Dependencies explicitly declared by the user.
        implicit_deps: Dependencies discovered by scanners or depfiles.
        builder: The builder that produces this node (None for sources).
        defined_at: Source location where this node was created.
    """

    __slots__ = ("explicit_deps", "implicit_deps", "builder", "defined_at", "_hash")

    def __init__(self, *, defined_at: SourceLocation | None = None) -> None:
        self.explicit_deps: list[Node] = []
        self.implicit_deps: list[Node] = []
        self.builder: Builder | None = None
        self.defined_at = defined_at or get_caller_location()
        self._hash: int | None = None

    @property
    def deps(self) -> list[Node]:
        """All direct dependencies of this node (explicit + implicit)."""
        return self.explicit_deps + self.implicit_deps

    def depends(self, *nodes: Node | Sequence[Node]) -> None:
        """Add explicit dependencies (nodes or sequences of nodes)."""
        for item in nodes:
            if isinstance(item, Node):
                self.explicit_deps.append(item)
            else:
                self.explicit_deps.extend(item)

    @property
    def is_source(self) -> bool:
        """True if this node is a source (not built by any builder)."""
        return self.builder is None

    @property
    def is_target(self) -> bool:
        """True if this node is a target (built by a builder)."""
        return self.builder is not None

    @property
    @abstractmethod
    def name(self) -> str:
        """A human-readable name for this node."""
        ...

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.name!r})"


PathRole = Literal["source", "build_output", "install_output"]
"""Tells generators how to render a node's path.

The three roles make the source / build / external trichotomy explicit:

- ``"source"``: an input file, relative to the project root (emitted via
  ``$topdir`` so it resolves from the build directory).
- ``"build_output"``: produced under the build directory (emitted relative
  to it).
- ``"install_output"``: produced *outside* the build directory, e.g.
  ``<root>/dist/bin/app``. Emitted like a source (``$topdir``-relative, or
  absolute only when it falls outside the project root) so build files stay
  relocatable instead of baking in an absolute install path.

Currently only ``"install_output"`` is set explicitly. A node with role
``None`` is classified implicitly from whether it has a builder: no builder
means a source, a builder means a build output.
"""


class FileNode(Node):
    """A node representing a file in the filesystem.

    FileNodes can be either source files (exist on disk, not built)
    or target files (generated by a builder).

    Attributes:
        path: The path to the file.
        role: Optional path role (see PathRole). ``None`` means a normal
              source or build output, distinguished by whether it has a
              builder.
        _build_info: Builder-specific information for code generation
                     (see BuildInfo).
    """

    __slots__ = ("path", "role", "_build_info")

    def __init__(
        self,
        path: Path | str,
        *,
        role: PathRole | None = None,
        defined_at: SourceLocation | None = None,
    ) -> None:
        super().__init__(defined_at=defined_at)
        self.path = Path(path) if isinstance(path, str) else path
        self.role: PathRole | None = role
        self._build_info: BuildInfo | None = None

    @property
    def name(self) -> str:
        return str(self.path)

    def exists(self) -> bool:
        """Check if the file exists on disk."""
        return self.path.exists()

    @property
    def suffix(self) -> str:
        """The file extension (e.g., '.cpp', '.o')."""
        return self.path.suffix

    def __str__(self) -> str:
        """User-friendly string representation for debugging."""
        parts = [f"FileNode: {self.path}"]
        if self.defined_at:
            parts.append(f" (defined at {self.defined_at})")
        if self._build_info:
            tool = self._build_info.get("tool", "?")
            parts.append(f" [built by {tool}]")
        return "".join(parts)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, FileNode):
            return NotImplemented
        return self.path == other.path

    def __hash__(self) -> int:
        if self._hash is None:
            self._hash = hash(("FileNode", self.path))
        return self._hash


class DirNode(Node):
    """A node representing a directory. Not currently used in production.

    As a target, the directory is up-to-date when all its registered members
    (add_member) are; as a source it represents only the declared files; as
    an order-only dependency it just ensures the directory exists.

    Attributes:
        path: The path to the directory.
        members: Files that belong to this directory (when used as target).
    """

    __slots__ = ("path", "role", "members")

    def __init__(
        self,
        path: Path | str,
        *,
        role: PathRole | None = None,
        defined_at: SourceLocation | None = None,
    ) -> None:
        super().__init__(defined_at=defined_at)
        self.path = Path(path) if isinstance(path, str) else path
        self.role: PathRole | None = role
        self.members: list[FileNode] = []

    @property
    def name(self) -> str:
        return str(self.path)

    def exists(self) -> bool:
        """Check if the directory exists on disk."""
        return self.path.exists() and self.path.is_dir()

    def add_member(self, node: FileNode) -> None:
        """Add a file as a member of this directory."""
        self.members.append(node)

    def __str__(self) -> str:
        """User-friendly string representation for debugging."""
        parts = [f"DirNode: {self.path}"]
        if self.defined_at:
            parts.append(f" (defined at {self.defined_at})")
        if self.members:
            parts.append(f" [{len(self.members)} members]")
        return "".join(parts)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, DirNode):
            return NotImplemented
        return self.path == other.path

    def __hash__(self) -> int:
        if self._hash is None:
            self._hash = hash(("DirNode", self.path))
        return self._hash


class ValueNode(Node):
    """A computed value (config hash, version string, ...) that can trigger
    rebuilds when it changes.

    Attributes:
        value_name: A unique name identifying this value.
        value: The actual value (any hashable type).
    """

    __slots__ = ("value_name", "value")

    def __init__(
        self,
        value_name: str,
        value: Any = None,
        *,
        defined_at: SourceLocation | None = None,
    ) -> None:
        super().__init__(defined_at=defined_at)
        self.value_name = value_name
        self.value = value

    @property
    def name(self) -> str:
        return f"Value({self.value_name})"

    def set_value(self, value: Any) -> None:
        """Update the value."""
        self.value = value

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ValueNode):
            return NotImplemented
        return self.value_name == other.value_name

    def __hash__(self) -> int:
        if self._hash is None:
            self._hash = hash(("ValueNode", self.value_name))
        return self._hash


class AliasNode(Node):
    """A named group of targets (a Ninja phony rule); no file of its own.

    Target references added via add_deferred_target() are resolved lazily:
    their output_nodes are read when ``targets`` is accessed, so an alias can
    reference targets whose output_nodes are populated later by resolve().

    Attributes:
        alias_name: The name of this alias.
        targets: The nodes this alias refers to (read-only property).
    """

    __slots__ = ("alias_name", "_nodes", "_target_refs")

    def __init__(
        self,
        alias_name: str,
        targets: Sequence[Node] | None = None,
        *,
        defined_at: SourceLocation | None = None,
    ) -> None:
        super().__init__(defined_at=defined_at)
        self.alias_name = alias_name
        self._nodes: list[Node] = list(targets) if targets else []
        self._target_refs: list[Target] = []

    @property
    def name(self) -> str:
        return self.alias_name

    @property
    def targets(self) -> list[Node]:
        """Nodes this alias refers to, including lazily-resolved targets."""
        result = list(self._nodes)
        for t in self._target_refs:
            nodes = t.output_nodes if t.output_nodes else t.nodes
            if not nodes:
                logger.warning(
                    "Alias '%s': target '%s' has no output nodes "
                    "(was resolve() called?)",
                    self.alias_name,
                    t.name,
                )
            result.extend(nodes)
        return result

    def add_target(self, node: Node) -> None:
        """Add a node to this alias."""
        self._nodes.append(node)

    def add_targets(self, nodes: Sequence[Node]) -> None:
        """Add multiple nodes to this alias."""
        self._nodes.extend(nodes)

    def add_deferred_target(self, target: Target) -> None:
        """Add a target whose nodes will be resolved lazily."""
        self._target_refs.append(target)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, AliasNode):
            return NotImplemented
        return self.alias_name == other.alias_name

    def __hash__(self) -> int:
        if self._hash is None:
            self._hash = hash(("AliasNode", self.alias_name))
        return self._hash
