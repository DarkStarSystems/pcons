# SPDX-License-Identifier: MIT
"""Builder protocol and base implementation.

A Builder creates target nodes from source nodes, using a specific tool.
Each tool provides one or more builders (e.g., a C compiler provides
an Object builder that turns .c files into .o files).
"""

from __future__ import annotations

import re
import uuid
from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast, runtime_checkable

from pcons.core.node import BuildInfo, FileNode, Node, OutputInfo
from pcons.core.subst import PathToken, SourcePath, TargetPath
from pcons.util.source_location import SourceLocation, get_caller_location

if TYPE_CHECKING:
    from pcons.core.environment import Environment
    from pcons.core.toolconfig import ToolConfig


@dataclass
class OutputSpec:
    """Specification for a builder output."""

    name: str  # Output name for variable reference (e.g. "import_lib")
    suffix: str  # File suffix (e.g. ".lib")
    implicit: bool = False  # Ninja implicit output (|)
    required: bool = True  # Must always be produced


class OutputGroup:
    """Container for multiple output nodes with named access.

    Allows outputs.primary, outputs.import_lib, outputs["import_lib"], and
    behaves like an iterable of nodes (e.g. objs += env.link.SharedLibrary(...)).
    """

    def __init__(self, nodes: dict[str, FileNode], primary_name: str) -> None:
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
        target: str | Path | Node | None,
        sources: list[str | Path | Node],
        **kwargs: Any,
    ) -> list[Node] | OutputGroup:
        """Build targets from sources (target=None auto-generates paths)."""
        ...


def apply_depends(
    nodes: list[Node] | OutputGroup,
    depends: Any,
    env: Environment | None,
) -> None:
    """Attach ``depends=`` items to the build statements of *nodes*.

    Accepts a single item or a sequence of nodes, Targets, strings and Paths,
    and adds each as an implicit dependency (see ``Node.depends``). Secondary
    outputs of a multi-output build are skipped: their build statement is the
    primary node's, and naming a dependency twice there would duplicate it.
    """
    from pcons.core.target import Target

    items = depends if isinstance(depends, (list, tuple)) else [depends]
    project = getattr(env, "_project", None) if env is not None else None

    dep_nodes: list[Node] = []
    for item in items:
        if isinstance(item, Node):
            dep_nodes.append(item)
        elif isinstance(item, Target):
            if not item.output_nodes:
                from pcons.core.errors import PconsError

                raise PconsError(
                    f"depends=[{item.name!r}]: that target's outputs don't "
                    f"exist yet (targets resolve later than builder calls). "
                    f"Depend on a Target from another Target — "
                    f"my_target.depends({item.name}) — or name the file "
                    f"directly here.",
                    location=get_caller_location(),
                )
            dep_nodes.extend(item.output_nodes)
        elif project is not None:
            dep_nodes.append(project.node(item))
        else:
            dep_nodes.append(FileNode(item, defined_at=get_caller_location()))

    for node in nodes:
        build_info = getattr(node, "_build_info", None) or {}
        if "primary_node" in build_info:
            continue
        node.depends(dep_nodes)


def reject_unknown_kwargs(builder_name: str, kwargs: dict[str, Any]) -> None:
    """Raise on builder keyword arguments nothing consumes.

    Quietly dropping a kwarg (``depends=`` used to be dropped this way) turns
    a typo or an unsupported option into a build that is wrong but silent.
    """
    unknown = sorted(k for k in kwargs if k != "defined_at")
    if unknown:
        raise TypeError(
            f"{builder_name}() got unexpected keyword argument(s): "
            f"{', '.join(unknown)}."
        )


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
        target: str | Path | Node | None,
        sources: list[str | Path | Node],
        **kwargs: Any,
    ) -> list[Node] | OutputGroup:
        """Build targets from sources: normalize inputs, delegate to _build().

        ``depends=`` is handled here so every builder honours it identically,
        with the same meaning it has on ``Target`` and ``env.Command``: an
        implicit dependency, ordered before the build but kept off the
        command line.
        """
        depends = kwargs.pop("depends", None)
        source_nodes = self._normalize_sources(sources, env)

        if target is None:
            target_paths = self._default_targets(source_nodes, env)
        elif isinstance(target, Node):
            target_paths = [Path(target.name)]
        else:
            target_paths = [Path(target)]

        result = self._build(env, target_paths, source_nodes, **kwargs)
        if depends:
            apply_depends(result, depends, env)
        return result

    def _normalize_sources(
        self,
        sources: list[str | Path | Node],
        env: Environment | None = None,
    ) -> list[Node]:
        """Convert sources to nodes, via project.node() (dedup) when available."""
        result: list[Node] = []
        project = getattr(env, "_project", None) if env else None
        for src in sources:
            if isinstance(src, Node):
                result.append(src)
            elif project is not None:
                result.append(project.node(src))
            else:
                result.append(FileNode(src, defined_at=get_caller_location()))
        return result

    def _default_targets(
        self,
        sources: list[Node],
        env: Environment,
    ) -> list[Path]:
        """Default target paths: source names in build_dir with the first
        target suffix. Subclasses can override."""
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
    ) -> list[Node] | OutputGroup:
        """Create the target nodes with dependencies and builder references."""
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
        restat: bool = False,
    ) -> None:
        """Initialize a command builder.

        Args:
            command_var: Variable holding the command template
                (e.g. 'cmdline' for $cc.cmdline).
            single_source: If True, create one target per source;
                otherwise all sources go to one target.
            depfile: TargetPath(suffix=".d") to derive the depfile path
                from the target output, or None.
            deps_style: Dependency style for Ninja ("gcc" or "msvc").
            restat: If True, Ninja re-checks output timestamp after build.
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
        self._restat = restat

    def _build(
        self,
        env: Environment,
        targets: list[Path],
        sources: list[Node],
        **kwargs: Any,
    ) -> list[Node] | OutputGroup:
        """Create target nodes for command execution."""
        reject_unknown_kwargs(self.name, kwargs)
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
        project = getattr(env, "_project", None)
        node = (
            project.node(target)
            if project is not None
            else FileNode(target, defined_at=defined_at)
        )
        node.add_inputs(sources)
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

        node._build_info = BuildInfo(
            tool=self._tool_name,
            command_var=self._command_var,
            language=self._language,
            sources=sources,
            depfile=depfile,
            deps_style=self._deps_style,
            restat=self._restat,
            env=env,
        )

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

        Parameters are those of CommandBuilder, plus ``outputs``: the
        OutputSpecs defining the outputs, first is primary.
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

    def _build(
        self,
        env: Environment,
        targets: list[Path],
        sources: list[Node],
        **kwargs: Any,
    ) -> list[Node] | OutputGroup:
        """Create target nodes; returns an OutputGroup for a single build."""
        reject_unknown_kwargs(self.name, kwargs)
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
        """Create all output nodes for a single build."""
        nodes: dict[str, FileNode] = {}
        primary_name = self._outputs[0].name
        project = getattr(env, "_project", None)

        # Create a node for each output
        for spec in self._outputs:
            if spec.name == primary_name:
                # Primary output uses the provided target path
                target_path = primary_target
            else:
                # Other outputs derive path from primary
                target_path = primary_target.with_suffix(spec.suffix)

            node = (
                project.node(target_path)
                if project is not None
                else FileNode(target_path, defined_at=defined_at)
            )
            node.builder = self
            nodes[spec.name] = node

        # Primary node has dependencies on sources
        primary_node = nodes[primary_name]
        primary_node.add_inputs(sources)

        # Build info lives on the primary node, covering all outputs
        output_info: dict[str, OutputInfo] = {
            spec.name: {
                "path": nodes[spec.name].path,
                "suffix": spec.suffix,
                "implicit": spec.implicit,
                "required": spec.required,
            }
            for spec in self._outputs
        }

        depfile: PathToken | None = None
        if self._depfile is not None:
            depfile = PathToken(
                prefix=self._depfile.prefix,
                path=str(primary_target),
                path_type="build",
                suffix=self._depfile.suffix,
            )

        primary_node._build_info = BuildInfo(
            tool=self._tool_name,
            command_var=self._command_var,
            language=self._language,
            sources=sources,
            outputs=output_info,
            all_output_nodes=nodes,
            depfile=depfile,
            deps_style=self._deps_style,
            env=env,
        )

        # Secondary nodes reference the primary for build info
        for name, node in nodes.items():
            if name != primary_name:
                node._build_info = {
                    "primary_node": primary_node,
                    "output_name": name,
                }

        return OutputGroup(nodes, primary_name)


#: A ${...} substitution whose contents are a plain variable name. Anything
#: else inside ${...} is either a marker form we recognize or a mistake.
_PLAIN_VARIABLE = re.compile(r"^\$\{[A-Za-z_][A-Za-z0-9_.]*\}$")

#: The substitutions env.Command() understands, for error messages.
_SUPPORTED_SUBSTITUTIONS = (
    "$SOURCE, $SOURCES, $TARGET, $TARGETS, ${SOURCES[n]}, ${TARGETS[n]}, "
    "${SOURCES[n:m]} (slices, with either end optional), $SRCDIR, "
    "${VARIABLE}, and $$ for a literal dollar"
)


#: A $SOURCE(S)/$TARGET(S) marker, optionally braced and — for the plural
#: forms — optionally subscripted with an index or a half-open range. Matched
#: anywhere in a token, not just as the whole of one, so that surrounding text
#: like the "./" of ./${SOURCES[0]} can come along as a prefix.
_MARKER = re.compile(
    r"\$(?:"
    r"\{(?:(?P<sliced>SOURCES|TARGETS)\[(\d*)(?::(\d*))?\]"
    r"|(?P<braced>SOURCES|TARGETS|SOURCE|TARGET))\}"
    r"|(?P<bare>SOURCES|TARGETS|SOURCE|TARGET)\b"
    r")"
)


def _marker_from_match(token: str, match: re.Match[str]) -> SourcePath | TargetPath:
    """Build the marker a matched $SOURCE/$TARGET substitution stands for."""
    kind = match.group("sliced") or match.group("braced") or match.group("bare")
    marker = SourcePath if kind.startswith("SOURCE") else TargetPath

    if match.group("sliced") is None:  # bare $SOURCES / ${TARGET} — all of them
        return marker()

    first, second = match.group(2), match.group(3)

    if second is None:  # ${X[n]} -- a single item
        if not first:
            raise ValueError(
                f"{token} has an empty subscript. Use ${{{kind}[n]}} for one "
                f"item or ${{{kind}[n:m]}} for a range."
            )
        return marker(index=int(first))

    start = int(first) if first else None
    stop = int(second) if second else None
    if start is None and stop is None:
        # ${X[:]} is just all of them; the bare form says so more clearly.
        return marker()
    return marker(start=start, stop=stop)


def _reject_unknown_substitution(token: str) -> None:
    """Raise on a ${...} that is neither a known marker nor a variable.

    Left alone, an unrecognized form reaches build.ninja as a shell-escaped
    literal and the command runs with nonsense in it -- the exact silent
    failure pcons's fail-fast rule exists to prevent. Variable references
    pass through here and are validated against the environment later.
    """
    for candidate in re.findall(r"\$\{[^}]*\}", token):
        if _PLAIN_VARIABLE.match(candidate):
            continue
        raise ValueError(
            f"Unrecognized substitution {candidate} in command {token!r}.\n"
            f"Supported: {_SUPPORTED_SUBSTITUTIONS}."
        )


def _reject_hand_quoting(token: str) -> None:
    """Raise on a token the author quoted themselves.

    pcons keeps a command as tokens and the generator quotes each one for the
    shell it writes for, so quoting a path that might contain spaces — the
    reflex in every other build system — quotes it twice: the program is
    handed ``"/path/with spaces/tool"``, quotes included, and reports that no
    such file exists.

    A leading *or* trailing quote counts, not just a matched pair: a string
    command is split on whitespace first, so the case quoting exists for
    (``"/opt/my tools/rez"``) arrives here already torn into ``"/opt/my`` and
    ``tools/rez"``.

    A token carrying shell syntax of its own (``a && b``) is left alone: the
    generator doesn't quote those either, so their quoting is the author's to
    get right — see ``_SHELL_OPERATORS`` in ``pcons.core.subst``.
    """
    from pcons.core.subst import _SHELL_OPERATORS

    if not token or (token[0] not in "\"'" and token[-1] not in "\"'"):
        return
    if any(operator in token.split() for operator in _SHELL_OPERATORS):
        return
    bare = token.strip("\"'")
    raise ValueError(
        f"Command token {token!r} is quoted. pcons quotes each token for the "
        f"shell itself, so this would reach the program with the quotes still "
        f"on it.\n"
        f"  Write it bare: {bare!r}.\n"
        f"  A string command is split on whitespace, so a token that must "
        f"contain a space needs the list form instead: "
        f"command=[..., {bare!r}, ...]."
    )


def _tokenize_one(token: Any) -> Any:
    """Turn one command token into a marker, or leave it as it is.

    A token holding exactly one $SOURCE/$TARGET marker becomes a
    SourcePath/TargetPath, carrying whatever text surrounded it as the
    marker's prefix and suffix — that is what makes ``./${SOURCES[0]}`` run
    a program this build produced, and ``/Fo$TARGET`` an output flag.
    """
    if not isinstance(token, str):
        return token
    _reject_hand_quoting(token)
    if "$" not in token:
        return token

    match = _MARKER.search(token)
    if match is None:
        _reject_unknown_substitution(token)
        # Keep as string (plain tokens and variable references)
        return token

    if _MARKER.search(token, match.end()):
        raise ValueError(
            f"Command token {token!r} has more than one $SOURCE/$TARGET "
            f"substitution. Give each one its own argument."
        )

    marker = _marker_from_match(token, match)
    prefix, suffix = token[: match.start()], token[match.end() :]
    if suffix.startswith("["):
        kind = match.group("braced") or match.group("bare")
        raise ValueError(
            f"Command token {token!r} needs braces around the subscript: "
            f"write ${{{kind}{suffix}}}, not ${kind}{suffix}."
        )
    _reject_unknown_substitution(prefix + suffix)

    if not prefix and not suffix:
        return marker

    # Text attached to a marker that can expand to several paths belongs to
    # each of them -- "-i${SOURCES[0:]}" means -ione.c -itwo.c, not one -i
    # welded to the first path. Saying so as a full slice makes the
    # generators, which already repeat a slice per path, do exactly that.
    if marker.index is None and not marker.is_slice:
        marker = replace(marker, start=0)
    return replace(marker, prefix=prefix, suffix=suffix)


class GenericCommandBuilder(BaseBuilder):
    """A builder for arbitrary shell commands, with $SOURCE/$TARGET-style
    variable substitution.

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

        # Run a tool this build produced, over the remaining sources
        env.Command(
            "all.txt",
            [tool, "a.txt", "b.txt"],
            "./${SOURCES[0]} $TARGET ${SOURCES[1:]}"
        )
    """

    def __init__(
        self,
        command: str | list[str],
        *,
        rule_name: str | None = None,
        restat: bool = False,
        cwd: Path | None = None,
    ) -> None:
        """Initialize a generic command builder.

        Args:
            command: The shell command. Supports $SOURCE(S), $TARGET(S),
                indexed ${SOURCES[n]}/${TARGETS[n]}, sliced
                ${SOURCES[n:m]}, and $$ for a literal dollar sign. Any of
                them may be part of a larger argument, as in
                ./${SOURCES[0]} or /Fo$TARGET.
            rule_name: Optional custom Ninja rule name; auto-generated
                if not provided.
            restat: If True, Ninja re-stats the output after the command,
                so unchanged output doesn't rebuild downstream targets.
            cwd: Absolute directory to run the command in, instead of the
                build directory. Generators render this edge's paths as seen
                from there; see the generators' ``_run_in_dir``.
        """
        # uuid4 gives uniqueness without thread synchronization
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
        self._restat = restat
        self._cwd = cwd

    def _tokenize_command(self, command: str | list[str]) -> list:
        """Convert command string to tokenized list with typed markers.

        Converts $SOURCE/$TARGET patterns to SourcePath()/TargetPath()
        markers, including the indexed (``${SOURCES[0]}``) and sliced
        (``${SOURCES[1:]}``) forms.

        A marker may sit inside a larger token: the text around it becomes
        the marker's prefix and suffix, so ``./${SOURCES[0]}`` runs a
        just-built tool and ``/Fo$TARGET`` names an output.
        """
        tokens = command.split() if isinstance(command, str) else list(command)

        return [_tokenize_one(token) for token in tokens]

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

    def _expand_command(self, env: Environment) -> list:
        """Expand the $variables in the command against the environment.

        The one place a Command's variables are substituted: a list-valued
        variable becomes several tokens, and ``$$`` collapses to a single
        literal ``$`` here rather than at the generator, which by then has no
        way to tell an escaped dollar from a real one and escapes it twice.

        $SOURCE/$TARGET are typed markers by now, but text a marker carries
        alongside its path is substituted like anything else. $SRCDIR is left
        for the generators, which alone know how to spell the source root.
        """

        def expand(text: str) -> str:
            if "$" not in text or "$SRCDIR" in text:
                return text
            tokens = env.subst_list([text])
            if len(tokens) != 1:
                raise ValueError(
                    f"{text!r} expands to {len(tokens)} arguments, but it is "
                    f"attached to a $SOURCE/$TARGET path."
                )
            return tokens[0]

        expanded: list = []
        for token in self._command:
            if isinstance(token, (SourcePath, TargetPath)):
                expanded.append(
                    replace(
                        token, prefix=expand(token.prefix), suffix=expand(token.suffix)
                    )
                )
            elif isinstance(token, str) and "$" in token and "$SRCDIR" not in token:
                # Substitute in list form so the token is never re-split on
                # whitespace: a quoted argument stays one argument.
                expanded.extend(env.subst_list([token]))
            else:
                expanded.append(token)
        return expanded

    def _build(
        self,
        env: Environment,
        targets: list[Path],
        sources: list[Node],
        **kwargs: Any,
    ) -> list[Node]:
        """Create target nodes for the command."""
        reject_unknown_kwargs(self.name, kwargs)
        defined_at = kwargs.get("defined_at") or get_caller_location()
        project = getattr(env, "_project", None)

        result: list[FileNode] = []
        for target in targets:
            node = (
                project.node(target)
                if project is not None
                else FileNode(target, defined_at=defined_at)
            )
            node.add_inputs(sources)
            node.builder = self
            result.append(node)

        command = self._expand_command(env)

        # Build info lives on the first (primary) target
        if result:
            primary = result[0]
            primary._build_info = {
                "tool": "command",
                "command_var": "cmdline",
                "command": command,
                "rule_name": self._rule_name,
                "language": None,
                "sources": sources,
                "all_targets": result,
                "depfile": None,
                "deps_style": None,
                "restat": self._restat,
                "cwd": self._cwd,
            }

            # For multiple outputs, mark secondary targets as referencing primary
            for secondary in result[1:]:
                secondary._build_info = {
                    "primary_node": primary,
                    "output_name": str(secondary.path),
                }

        return cast(list[Node], result)
