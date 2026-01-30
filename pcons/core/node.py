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
from typing import TYPE_CHECKING, Any, TypedDict

from pcons.util.source_location import SourceLocation, get_caller_location

if TYPE_CHECKING:
    from pcons.core.builder import Builder
    from pcons.core.subst import PathToken
    from pcons.core.target import Target
    from pcons.tools.toolchain import ToolchainContext

logger = logging.getLogger(__name__)


class OutputInfo(TypedDict, total=False):
    """Information about a single output in a multi-output build.

    Attributes:
        path: Path to the output file.
        suffix: File suffix for the output.
        implicit: If True, this is an implicit output (not tracked by Ninja).
        required: If True, this output must be generated.
    """

    path: Path
    suffix: str
    implicit: bool
    required: bool


class BuildInfo(TypedDict, total=False):
    """Build information stored on nodes for code generation.

    This TypedDict documents all the fields that can appear in a node's
    _build_info dictionary. Different builders populate different subsets
    of these fields.

    Common fields (most builders):
        tool: Name of the tool to use (e.g., "cc", "cxx", "link", "ar", "copy").
        command_var: Variable name containing command template (e.g., "objcmd").
        language: Language for linker selection (e.g., "c", "cxx").
        sources: List of source Node objects.
        depfile: Depfile path pattern for Ninja (e.g., "$out.d").
        deps_style: Dependency style for Ninja ("gcc" or "msvc").
        command: Direct command to run (for generic/custom builders).
        description: Human-readable description for build output.

    Toolchain context:
        context: ToolchainContext providing env overrides for command expansion.
                 The resolver uses context.get_env_overrides() to set values
                 on the environment before expanding command templates.

    Multi-output builds:
        outputs: Dict mapping output name to OutputInfo.
        all_output_nodes: Dict mapping output name to FileNode.
        primary_node: Reference to primary node (for secondary outputs).
        output_name: Name of this output (for secondary outputs).

    Generic command builder:
        rule_name: Custom rule name for Ninja.
        all_targets: List of all target nodes.
    """

    # Common fields
    tool: str
    command_var: str
    language: str | None
    sources: list[Any]  # list[Node], but avoid circular import
    depfile: PathToken | None  # PathToken with suffix for depfile path
    deps_style: str | None
    command: str | list[str]  # Command as string or list of tokens
    description: str  # Human-readable build description

    # Toolchain-provided context
    # Resolver uses context.get_env_overrides() for command expansion
    context: ToolchainContext | None

    # Multi-output builds
    outputs: dict[str, OutputInfo]
    all_output_nodes: dict[str, Any]  # dict[str, FileNode]
    primary_node: Any  # FileNode
    output_name: str

    # Generic command builder
    rule_name: str
    all_targets: list[Any]  # list[Node]

    # Per-build variables for standalone tools (Install, Archive)
    # These are written as Ninja build-level variables
    variables: dict[str, str]

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
        """Initialize a node.

        Args:
            defined_at: Source location where this node was created.
                       If None, captures the caller's location.
        """
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
        """Add explicit dependencies.

        Args:
            *nodes: Node(s) which must be up to date before building this one.
                   Can be individual nodes or sequences of nodes.
        """
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


class FileNode(Node):
    """A node representing a file in the filesystem.

    FileNodes can be either source files (exist on disk, not built)
    or target files (generated by a builder).

    Attributes:
        path: The path to the file.
        _build_info: Builder-specific information for code generation.
                    See BuildInfo TypedDict for documented fields.
    """

    __slots__ = ("path", "_build_info")

    def __init__(
        self,
        path: Path | str,
        *,
        defined_at: SourceLocation | None = None,
    ) -> None:
        """Create a file node.

        Args:
            path: Path to the file (will be converted to Path).
            defined_at: Source location where this node was created.
        """
        super().__init__(defined_at=defined_at)
        self.path = Path(path) if isinstance(path, str) else path
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
    """A node representing a directory.

    Not currently used in production. This class exists as the intended
    abstraction for directory nodes and could be wired into Install/InstallDir
    builders in the future.

    Directory nodes have different semantics depending on usage:

    As a target:
        The directory is up-to-date when all its member files are up-to-date.
        Members are explicitly registered via add_member().

    As a source:
        Represents the directory and all declared files within it.
        Files not declared in the build are ignored.

    As an order-only dependency:
        Just ensures the directory exists before dependents are built.

    Attributes:
        path: The path to the directory.
        members: Files that belong to this directory (when used as target).
    """

    __slots__ = ("path", "members")

    def __init__(
        self,
        path: Path | str,
        *,
        defined_at: SourceLocation | None = None,
    ) -> None:
        """Create a directory node.

        Args:
            path: Path to the directory.
            defined_at: Source location where this node was created.
        """
        super().__init__(defined_at=defined_at)
        self.path = Path(path) if isinstance(path, str) else path
        self.members: list[FileNode] = []

    @property
    def name(self) -> str:
        return str(self.path)

    def exists(self) -> bool:
        """Check if the directory exists on disk."""
        return self.path.exists() and self.path.is_dir()

    def add_member(self, node: FileNode) -> None:
        """Add a file as a member of this directory.

        When this directory is used as a target, it's up-to-date
        when all its members are up-to-date.

        Args:
            node: A file node that belongs to this directory.
        """
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
    """A node representing a computed value.

    ValueNodes are used for things like configuration hashes,
    version strings, or other computed values that can trigger
    rebuilds when they change.

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
        """Create a value node.

        Args:
            value_name: A unique name for this value.
            value: The value (can be set later).
            defined_at: Source location where this node was created.
        """
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
    """A node representing a named group of targets (phony target).

    AliasNodes don't correspond to files - they're just names
    that group other targets together. In Ninja, these become
    phony rules.

    Target references added via add_deferred_target() are resolved lazily:
    their output_nodes are read when the ``targets`` property is accessed,
    not when the alias is created. This allows aliases to reference targets
    whose output_nodes are populated later during resolve().

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
        """Create an alias node.

        Args:
            alias_name: The name of the alias (e.g., "all", "test").
            targets: Initial targets for this alias.
            defined_at: Source location where this node was created.
        """
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
