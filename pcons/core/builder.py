# SPDX-License-Identifier: MIT
"""Builder protocol and base implementation.

A Builder creates target nodes from source nodes, using a specific tool.
Each tool provides one or more builders (e.g., a C compiler provides
an Object builder that turns .c files into .o files).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from pcons.core.node import FileNode, Node
from pcons.util.source_location import SourceLocation, get_caller_location

if TYPE_CHECKING:
    from pcons.core.environment import Environment
    from pcons.core.toolconfig import ToolConfig


@runtime_checkable
class Builder(Protocol):
    """Protocol for builders.

    A Builder knows how to create target files from source files.
    It's associated with a specific tool and may produce files
    with specific suffixes.
    """

    @property
    def name(self) -> str:
        """Builder name (e.g., 'Object', 'StaticLibrary')."""
        ...

    @property
    def tool_name(self) -> str:
        """Name of the tool this builder belongs to."""
        ...

    @property
    def src_suffixes(self) -> list[str]:
        """File suffixes this builder accepts as input."""
        ...

    @property
    def target_suffixes(self) -> list[str]:
        """File suffixes this builder produces."""
        ...

    @property
    def language(self) -> str | None:
        """Language this builder compiles (for linker selection)."""
        ...

    def __call__(
        self,
        env: Environment,
        target: str | Path | None,
        sources: list[str | Path | Node],
        **kwargs: Any,
    ) -> list[Node]:
        """Build targets from sources.

        Args:
            env: The build environment.
            target: Target file path, or None to auto-generate.
            sources: Source files or nodes.
            **kwargs: Additional builder-specific options.

        Returns:
            List of created target nodes.
        """
        ...


class BaseBuilder(ABC):
    """Abstract base class for builders.

    Provides common functionality for builders. Subclasses must implement
    _build() to do the actual target creation.
    """

    def __init__(
        self,
        name: str,
        tool_name: str,
        *,
        src_suffixes: list[str] | None = None,
        target_suffixes: list[str] | None = None,
        language: str | None = None,
    ) -> None:
        """Initialize a builder.

        Args:
            name: Builder name.
            tool_name: Name of the tool this builder belongs to.
            src_suffixes: Accepted input suffixes (e.g., ['.c', '.h']).
            target_suffixes: Output suffixes (e.g., ['.o']).
            language: Language for linker selection (e.g., 'c', 'cxx').
        """
        self._name = name
        self._tool_name = tool_name
        self._src_suffixes = src_suffixes or []
        self._target_suffixes = target_suffixes or []
        self._language = language

    @property
    def name(self) -> str:
        return self._name

    @property
    def tool_name(self) -> str:
        return self._tool_name

    @property
    def src_suffixes(self) -> list[str]:
        return self._src_suffixes

    @property
    def target_suffixes(self) -> list[str]:
        return self._target_suffixes

    @property
    def language(self) -> str | None:
        return self._language

    def __call__(
        self,
        env: Environment,
        target: str | Path | None,
        sources: list[str | Path | Node],
        **kwargs: Any,
    ) -> list[Node]:
        """Build targets from sources.

        Normalizes inputs and delegates to _build().
        """
        # Normalize sources to nodes
        source_nodes = self._normalize_sources(sources)

        # Get target path(s)
        if target is None:
            target_paths = self._default_targets(source_nodes, env)
        else:
            target_paths = [Path(target) if isinstance(target, str) else target]

        # Build
        return self._build(env, target_paths, source_nodes, **kwargs)

    def _normalize_sources(
        self,
        sources: list[str | Path | Node],
    ) -> list[Node]:
        """Convert sources to nodes."""
        result: list[Node] = []
        for src in sources:
            if isinstance(src, Node):
                result.append(src)
            else:
                result.append(FileNode(src, defined_at=get_caller_location()))
        return result

    def _default_targets(
        self,
        sources: list[Node],
        env: Environment,
    ) -> list[Path]:
        """Generate default target paths from sources.

        Default implementation: replace suffix with first target suffix.
        Subclasses can override for different behavior.
        """
        if not self._target_suffixes:
            raise ValueError(f"Builder {self.name} has no target suffixes")

        build_dir = Path(env.get("build_dir", "build"))
        suffix = self._target_suffixes[0]

        result: list[Path] = []
        for src in sources:
            if isinstance(src, FileNode):
                # Put in build_dir with new suffix
                target = build_dir / src.path.with_suffix(suffix).name
                result.append(target)
        return result

    @abstractmethod
    def _build(
        self,
        env: Environment,
        targets: list[Path],
        sources: list[Node],
        **kwargs: Any,
    ) -> list[Node]:
        """Actually create the target nodes.

        Subclasses implement this to create FileNodes with proper
        dependencies and builder references.

        Args:
            env: Build environment.
            targets: Target file paths.
            sources: Source nodes.
            **kwargs: Builder-specific options.

        Returns:
            List of created target nodes.
        """
        ...

    def _get_tool_config(self, env: Environment) -> ToolConfig:
        """Get this builder's tool configuration from the environment."""
        config: ToolConfig = getattr(env, self._tool_name)
        return config

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.name!r}, tool={self.tool_name!r})"


class CommandBuilder(BaseBuilder):
    """A builder that runs a shell command.

    This is the most common type of builder - it generates a command
    line from a template and creates target nodes.
    """

    def __init__(
        self,
        name: str,
        tool_name: str,
        command_var: str,
        *,
        src_suffixes: list[str] | None = None,
        target_suffixes: list[str] | None = None,
        language: str | None = None,
        single_source: bool = False,
    ) -> None:
        """Initialize a command builder.

        Args:
            name: Builder name.
            tool_name: Tool name.
            command_var: Variable name containing command template
                        (e.g., 'cmdline' for $cc.cmdline).
            src_suffixes: Accepted input suffixes.
            target_suffixes: Output suffixes.
            language: Language for linker selection.
            single_source: If True, create one target per source.
                          If False, all sources go to one target.
        """
        super().__init__(
            name,
            tool_name,
            src_suffixes=src_suffixes,
            target_suffixes=target_suffixes,
            language=language,
        )
        self._command_var = command_var
        self._single_source = single_source

    def _build(
        self,
        env: Environment,
        targets: list[Path],
        sources: list[Node],
        **kwargs: Any,
    ) -> list[Node]:
        """Create target nodes for command execution."""
        tool_config = self._get_tool_config(env)
        defined_at = kwargs.get("defined_at") or get_caller_location()

        result: list[Node] = []

        if self._single_source:
            # One target per source - skip if no sources
            if not sources:
                return []
            for target, source in zip(targets, sources, strict=True):
                node = self._create_target_node(
                    env, tool_config, target, [source], defined_at
                )
                result.append(node)
        else:
            # All sources to one target
            if targets:
                node = self._create_target_node(
                    env, tool_config, targets[0], sources, defined_at
                )
                result.append(node)

        return result

    def _create_target_node(
        self,
        env: Environment,
        tool_config: ToolConfig,
        target: Path,
        sources: list[Node],
        defined_at: SourceLocation,
    ) -> FileNode:
        """Create a single target node."""
        node = FileNode(target, defined_at=defined_at)
        node.depends(sources)
        node.builder = self

        # Store build info for generator
        # These will be used by the generator to create ninja rules
        node._build_info = {
            "tool": self._tool_name,
            "command_var": self._command_var,
            "language": self._language,
            "sources": sources,
        }

        return node
