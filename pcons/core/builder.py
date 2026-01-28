# SPDX-License-Identifier: MIT
"""Builder protocol and base implementation.

A Builder creates target nodes from source nodes, using a specific tool.
Each tool provides one or more builders (e.g., a C compiler provides
an Object builder that turns .c files into .o files).
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast, runtime_checkable

from pcons.core.node import FileNode, Node
from pcons.core.subst import PathToken, TargetPath
from pcons.util.source_location import SourceLocation, get_caller_location

if TYPE_CHECKING:
    from pcons.core.environment import Environment
    from pcons.core.toolconfig import ToolConfig


@dataclass
class OutputSpec:
    """Specification for a builder output.

    Args:
        name: Output name for variable reference (e.g., "import_lib")
        suffix: File suffix (e.g., ".lib")
        implicit: If True, this is a Ninja implicit output (|)
        required: If True, must always be produced
    """

    name: str
    suffix: str
    implicit: bool = False  # Ninja implicit output
    required: bool = True


class OutputGroup:
    """Container for multiple output nodes with named access.

    Allows: outputs.primary, outputs.import_lib, outputs["import_lib"]
    Also supports list() for iteration and len().

    This class provides backward compatibility by behaving like a list
    when used in contexts that expect iterables (e.g., objs += env.link.SharedLibrary(...))
    """

    def __init__(self, nodes: dict[str, FileNode], primary_name: str) -> None:
        """Initialize an OutputGroup.

        Args:
            nodes: Dictionary mapping output names to FileNode instances
            primary_name: The name of the primary output in the nodes dict
        """
        self._nodes = nodes
        self._primary_name = primary_name

    @property
    def primary(self) -> FileNode:
        """Get the primary output node."""
        return self._nodes[self._primary_name]

    def __getattr__(self, name: str) -> FileNode:
        if name.startswith("_"):
            raise AttributeError(
                f"'{type(self).__name__}' object has no attribute '{name}'"
            )
        if name in self._nodes:
            return self._nodes[name]
        raise AttributeError(f"No output named '{name}'")

    def __getitem__(self, name: str) -> FileNode:
        return self._nodes[name]

    def __iter__(self) -> Iterator[FileNode]:
        return iter(self._nodes.values())

    def __len__(self) -> int:
        return len(self._nodes)

    def __list__(self) -> list[FileNode]:
        return list(self._nodes.values())

    def keys(self) -> list[str]:
        """Return the names of all outputs."""
        return list(self._nodes.keys())

    def values(self) -> list[FileNode]:
        """Return all output nodes."""
        return list(self._nodes.values())

    def items(self) -> list[tuple[str, FileNode]]:
        """Return all (name, node) pairs."""
        return list(self._nodes.items())

    def __repr__(self) -> str:
        return (
            f"OutputGroup({list(self._nodes.keys())}, primary={self._primary_name!r})"
        )


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
        depfile: TargetPath | None = None,
        deps_style: str | None = None,
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
            depfile: Depfile specification - TargetPath(suffix=".d") for depfile
                    path derived from target output, or None.
            deps_style: Dependency style for Ninja ("gcc" or "msvc").
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
        self._depfile = depfile
        self._deps_style = deps_style

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

        # Resolve depfile: convert TargetPath to PathToken with actual target path
        depfile: PathToken | None = None
        if self._depfile is not None:
            depfile = PathToken(
                prefix=self._depfile.prefix,
                path=str(target),
                path_type="build",
                suffix=self._depfile.suffix,
            )

        # Store build info for generator
        # These will be used by the generator to create ninja rules
        node._build_info = {
            "tool": self._tool_name,
            "command_var": self._command_var,
            "language": self._language,
            "sources": sources,
            "depfile": depfile,
            "deps_style": self._deps_style,
            "env": env,
        }

        return node


class MultiOutputBuilder(CommandBuilder):
    """Builder that produces multiple output files.

    Use for things like MSVC SharedLibrary which produces:
    - .dll (primary)
    - .lib (import library)
    - .exp (export file, implicit)

    Example:
        builder = MultiOutputBuilder(
            "SharedLibrary", "link", "sharedcmd",
            outputs=[
                OutputSpec("primary", ".dll"),
                OutputSpec("import_lib", ".lib"),
                OutputSpec("export_file", ".exp", implicit=True),
            ],
            src_suffixes=[".obj"],
        )
    """

    def __init__(
        self,
        name: str,
        tool_name: str,
        command_var: str,
        outputs: list[OutputSpec],
        *,
        src_suffixes: list[str] | None = None,
        language: str | None = None,
        single_source: bool = False,
        depfile: TargetPath | None = None,
        deps_style: str | None = None,
    ) -> None:
        """Initialize a multi-output builder.

        Args:
            name: Builder name.
            tool_name: Tool name.
            command_var: Variable name containing command template.
            outputs: List of OutputSpec defining the outputs. First is primary.
            src_suffixes: Accepted input suffixes.
            language: Language for linker selection.
            single_source: If True, create one target per source.
            depfile: Depfile specification - TargetPath(suffix=".d") for depfile
                    path derived from target output, or None.
            deps_style: Dependency style for Ninja ("gcc" or "msvc").
        """
        # Primary output determines target_suffixes
        primary = outputs[0]
        super().__init__(
            name,
            tool_name,
            command_var,
            src_suffixes=src_suffixes,
            target_suffixes=[primary.suffix],
            language=language,
            single_source=single_source,
            depfile=depfile,
            deps_style=deps_style,
        )
        self._outputs = outputs

    @property
    def outputs(self) -> list[OutputSpec]:
        """Get the output specifications."""
        return self._outputs

    def _build(  # type: ignore[override]
        self,
        env: Environment,
        targets: list[Path],
        sources: list[Node],
        **kwargs: Any,
    ) -> list[Node] | OutputGroup:
        """Create target nodes for command execution.

        For multi-output builders, returns an OutputGroup instead of a list
        when there are multiple outputs. The OutputGroup is iterable for
        backward compatibility.
        """
        tool_config = self._get_tool_config(env)
        defined_at = kwargs.get("defined_at") or get_caller_location()

        if self._single_source:
            # One OutputGroup per source - skip if no sources
            if not sources:
                return []
            result: list[Node] = []
            for target, source in zip(targets, sources, strict=True):
                output_group = self._create_multi_output_nodes(
                    env, tool_config, target, [source], defined_at
                )
                result.extend(output_group)
            return result
        else:
            # All sources to one OutputGroup
            if targets:
                return self._create_multi_output_nodes(
                    env, tool_config, targets[0], sources, defined_at
                )
            return []

    def _create_multi_output_nodes(
        self,
        env: Environment,
        tool_config: ToolConfig,
        primary_target: Path,
        sources: list[Node],
        defined_at: SourceLocation,
    ) -> OutputGroup:
        """Create multiple output nodes for a single build.

        Returns an OutputGroup containing all output nodes.
        """
        nodes: dict[str, FileNode] = {}
        primary_name = self._outputs[0].name

        # Create a node for each output
        for spec in self._outputs:
            if spec.name == primary_name:
                # Primary output uses the provided target path
                target_path = primary_target
            else:
                # Other outputs derive path from primary
                target_path = primary_target.with_suffix(spec.suffix)

            node = FileNode(target_path, defined_at=defined_at)
            node.builder = self
            nodes[spec.name] = node

        # Primary node has dependencies on sources
        primary_node = nodes[primary_name]
        primary_node.depends(sources)

        # Store build info on the primary node
        # Include information about all outputs for the generator
        output_info = {
            spec.name: {
                "path": nodes[spec.name].path,
                "suffix": spec.suffix,
                "implicit": spec.implicit,
                "required": spec.required,
            }
            for spec in self._outputs
        }

        primary_node._build_info = {  # type: ignore[assignment]
            "tool": self._tool_name,
            "command_var": self._command_var,
            "language": self._language,
            "sources": sources,
            "outputs": output_info,
            "all_output_nodes": nodes,
            "depfile": self._depfile,
            "deps_style": self._deps_style,
            "env": env,
        }

        # Secondary nodes reference the primary for build info
        for name, node in nodes.items():
            if name != primary_name:
                node._build_info = {
                    "primary_node": primary_node,
                    "output_name": name,
                }

        return OutputGroup(nodes, primary_name)


class GenericCommandBuilder(BaseBuilder):
    """A builder for arbitrary shell commands.

    This builder allows users to run arbitrary shell commands as part of
    the build process. It supports variable substitution for common patterns
    like $SOURCE, $TARGET, $SOURCES, $TARGETS.

    Example:
        # Generate a header from a template
        env.Command(
            "config.h",
            ["config.h.in", "version.txt"],
            "python generate_config.py $SOURCES > $TARGET"
        )

        # Run a code generator
        env.Command(
            ["parser.c", "parser.h"],
            "grammar.y",
            "bison -d -o ${TARGETS[0]} $SOURCE"
        )
    """

    def __init__(
        self,
        command: str | list[str],
        *,
        rule_name: str | None = None,
    ) -> None:
        """Initialize a generic command builder.

        Args:
            command: The shell command to run. Supports variable substitution:
                    - $SOURCE, $SOURCES: Source file(s)
                    - $TARGET, $TARGETS: Target file(s)
                    - ${SOURCES[n]}, ${TARGETS[n]}: Indexed access
            rule_name: Optional custom rule name for Ninja. If not provided,
                      a unique name is generated using uuid.
        """
        # Generate unique rule name if not provided
        # Using uuid4 ensures uniqueness without thread synchronization
        if rule_name is None:
            rule_name = f"command_{uuid.uuid4().hex[:8]}"

        super().__init__(
            name="Command",
            tool_name="command",
            src_suffixes=[],  # Accepts any source
            target_suffixes=[],  # Produces any target
            language=None,
        )

        # Convert command to tokenized list with SourcePath/TargetPath markers
        self._command = self._tokenize_command(command)
        self._rule_name = rule_name

    def _tokenize_command(self, command: str | list[str]) -> list:
        """Convert command string to tokenized list with typed markers.

        Converts $SOURCE/$TARGET patterns to SourcePath()/TargetPath() markers.
        Also handles indexed patterns like ${SOURCES[0]} and ${TARGETS[0]}.
        """
        import re

        from pcons.core.subst import SourcePath, TargetPath

        # Convert to token list
        if isinstance(command, str):
            tokens = command.split()
        else:
            tokens = list(command)

        # Patterns for indexed access
        indexed_source_pattern = re.compile(r"^\$\{SOURCES\[(\d+)\]\}$")
        indexed_target_pattern = re.compile(r"^\$\{TARGETS\[(\d+)\]\}$")

        # Replace string patterns with typed markers
        result: list = []
        for token in tokens:
            if token in ("$SOURCE", "$SOURCES"):
                result.append(SourcePath())
            elif token in ("$TARGET", "$TARGETS"):
                result.append(TargetPath())
            elif match := indexed_source_pattern.match(token):
                # ${SOURCES[n]} -> SourcePath(index=n)
                index = int(match.group(1))
                result.append(SourcePath(index=index))
            elif match := indexed_target_pattern.match(token):
                # ${TARGETS[n]} -> TargetPath(index=n)
                index = int(match.group(1))
                result.append(TargetPath(index=index))
            elif "$SOURCE" in token or "$TARGET" in token:
                # Token has embedded variables - keep as string for generator to handle
                # This covers cases like /Fo$TARGET or -MF$TARGET.d
                result.append(token)
            else:
                result.append(token)

        return result

    @property
    def command(self) -> list:
        """The command template as a tokenized list."""
        return self._command

    @property
    def rule_name(self) -> str:
        """The Ninja rule name for this command."""
        return self._rule_name

    def _default_targets(
        self,
        sources: list[Node],
        env: Environment,
    ) -> list[Path]:
        """Generic commands must have explicit targets."""
        raise ValueError(
            "GenericCommandBuilder requires explicit target(s). "
            "Use env.Command(target, sources, command) with a target specified."
        )

    def _build(
        self,
        env: Environment,
        targets: list[Path],
        sources: list[Node],
        **kwargs: Any,
    ) -> list[Node]:
        """Create target nodes for the command."""
        defined_at = kwargs.get("defined_at") or get_caller_location()

        # Create target nodes (all FileNode, tracked for type safety)
        result: list[FileNode] = []
        for target in targets:
            node = FileNode(target, defined_at=defined_at)
            node.depends(sources)
            node.builder = self
            result.append(node)

        # Store build info on the first (primary) target
        # This is used by the Ninja generator to create build rules
        if result:
            primary = result[0]
            primary._build_info = {
                "tool": "command",
                "command_var": "cmdline",
                "command": self._command,
                "rule_name": self._rule_name,
                "language": None,
                "sources": sources,
                "all_targets": result,
                "depfile": None,
                "deps_style": None,
            }

            # For multiple outputs, mark secondary targets as referencing primary
            for secondary in result[1:]:
                secondary._build_info = {
                    "primary_node": primary,
                    "output_name": str(secondary.path),
                }

        return cast(list[Node], result)
