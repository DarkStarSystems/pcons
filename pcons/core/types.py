# SPDX-License-Identifier: MIT
"""Core type definitions and Protocol classes.

This module provides type aliases and Protocol classes used across the pcons
codebase. By centralizing these definitions, we reduce circular import issues
and provide a single source of truth for shared types.

Type Aliases:
    SourceSpec: A union type for specifying sources (Target, Node, Path, or str)

Protocol Classes:
    TargetLike: Protocol for objects that behave like targets
    NodeLike: Protocol for objects that behave like nodes
    EnvironmentLike: Protocol for objects that behave like environments
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, Union, runtime_checkable

if TYPE_CHECKING:
    from pcons.core.builder import Builder
    from pcons.util.source_location import SourceLocation


# Type alias for source specifications
# This represents the various ways a source can be specified in the API
SourceSpec = Union["TargetLike", "NodeLike", Path, str]


@runtime_checkable
class NodeLike(Protocol):
    """Protocol for objects that behave like nodes in the dependency graph.

    This protocol captures the essential interface of a Node without
    requiring inheritance from the Node base class.
    """

    @property
    def name(self) -> str:
        """A human-readable name for this node."""
        ...

    @property
    def explicit_deps(self) -> list[Any]:
        """Dependencies explicitly declared by the user."""
        ...

    @property
    def implicit_deps(self) -> list[Any]:
        """Dependencies discovered by scanners or depfiles."""
        ...

    @property
    def builder(self) -> Builder | None:
        """The builder that produces this node (None for sources)."""
        ...

    def depends(self, *nodes: Any) -> None:
        """Add explicit dependencies."""
        ...


@runtime_checkable
class FileNodeLike(NodeLike, Protocol):
    """Protocol for objects that behave like file nodes.

    Extends NodeLike with file-specific attributes.
    """

    @property
    def path(self) -> Path:
        """The path to the file."""
        ...

    @property
    def suffix(self) -> str:
        """The file extension."""
        ...

    def exists(self) -> bool:
        """Check if the file exists on disk."""
        ...


@runtime_checkable
class TargetLike(Protocol):
    """Protocol for objects that behave like build targets.

    This protocol captures the essential interface of a Target without
    requiring inheritance from the Target class. Useful for type hints
    that need to accept target-like objects without creating import cycles.
    """

    @property
    def name(self) -> str:
        """Target name."""
        ...

    @property
    def target_type(self) -> str | None:
        """Type of target (static_library, shared_library, program, etc.)."""
        ...

    @property
    def sources(self) -> list[Any]:
        """Source nodes for this target."""
        ...

    @property
    def dependencies(self) -> list[Any]:
        """Other targets this depends on."""
        ...

    @property
    def nodes(self) -> list[Any]:
        """Output nodes created by building this target."""
        ...

    @property
    def output_nodes(self) -> list[Any]:
        """Final output nodes (library/program)."""
        ...

    @property
    def object_nodes(self) -> list[Any]:
        """Compiled object nodes."""
        ...

    @property
    def defined_at(self) -> SourceLocation | None:
        """Where this target was created in user code."""
        ...

    def link(self, *targets: Any) -> Any:
        """Add targets as dependencies."""
        ...

    def transitive_dependencies(self) -> list[Any]:
        """Return all dependencies transitively."""
        ...

    def get_all_languages(self) -> set[str]:
        """Get all languages required by this target and its dependencies."""
        ...


@runtime_checkable
class EnvironmentLike(Protocol):
    """Protocol for objects that behave like build environments.

    This protocol captures the essential interface of an Environment without
    requiring inheritance from the Environment class.
    """

    def has_tool(self, name: str) -> bool:
        """Check if a tool namespace exists."""
        ...

    def add_tool(self, name: str, config: Any = None) -> Any:
        """Add or get a tool namespace."""
        ...

    def register_node(self, node: Any) -> None:
        """Register a node created by a builder."""
        ...

    def subst(self, template: str | list[str], **extra: Any) -> str:
        """Expand variables in a template."""
        ...

    def clone(self) -> Any:
        """Create a deep copy of this environment."""
        ...


@runtime_checkable
class ProjectLike(Protocol):
    """Protocol for objects that behave like projects.

    This protocol captures the essential interface of a Project without
    requiring inheritance from the Project class.
    """

    @property
    def name(self) -> str:
        """Project name."""
        ...

    @property
    def root_dir(self) -> Path:
        """Project root directory."""
        ...

    @property
    def build_dir(self) -> Path:
        """Build output directory."""
        ...

    @property
    def targets(self) -> list[Any]:
        """All registered targets."""
        ...

    @property
    def environments(self) -> list[Any]:
        """All environments in this project."""
        ...

    def node(self, path: Path | str) -> Any:
        """Get or create a FileNode for a path."""
        ...
