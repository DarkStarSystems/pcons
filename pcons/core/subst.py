# SPDX-License-Identifier: MIT
"""Variable substitution engine for pcons.

Key design principles:
1. Lists stay as lists until final shell command generation
2. Function-style syntax for list operations: ${prefix(p, list)}
3. Shell quoting happens only at the end, appropriate for target shell
4. MultiCmd wrapper for multiple commands in a single build step

Supported syntax:
- Simple variables: $VAR or ${VAR}
- Namespaced variables: $tool.var or ${tool.var}
- Escaped dollars: $$ becomes literal $ (useful for shell variables like $$ORIGIN in rpath)
- Functions: ${prefix(var, list)}, ${suffix(list, var)}, ${wrap(p, list, s)},
             ${pairwise(var, list)} (produces interleaved pairs)

Command template forms:
- String: "$cc.cmd $cc.flags -c -o $TARGET $SOURCE" (auto-tokenized on whitespace)
- List: ["$cc.cmd", "$cc.flags", "-c", "-o", TargetPath(), SourcePath()]
- MultiCmd: MultiCmd(["cmd1 args", "cmd2 args"]) (multiple commands)

Generator-agnostic path markers, which generators render in native syntax:
- SourcePath() / TargetPath(): the edge's inputs and outputs. A command
  template writes them as $SOURCE/$SOURCES and $TARGET/$TARGETS; a toolchain
  constructs the marker directly.
- TargetPath(suffix=".d"): the depfile, in a depflags template.
- TargetPath(basename=True): the output's filename alone, for a flag that
  names the library it is building (an install name, a SONAME).
- NodeVar("NAME"): a per-edge value the toolchain attached to the node.
"""

from __future__ import annotations

import platform
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

from pcons.core.debug import is_enabled, trace
from pcons.core.errors import (
    CircularReferenceError,
    MissingVariableError,
    SubstitutionError,
)
from pcons.util.source_location import SourceLocation

# =============================================================================
# MultiCmd wrapper for multiple commands
# =============================================================================


@dataclass
class MultiCmd:
    """Wrapper for multiple commands in a single build step.

    Args:
        commands: List of commands (strings or token lists)
        join: How to join commands ("&&", ";", or "\\n")

    Example:
        MultiCmd([
            "mkdir -p $(dirname $$TARGET)",
            "$cc.cmd $cc.flags -c -o $$TARGET $$SOURCE"
        ])
    """

    commands: list[str | list[str]]
    join: str = "&&"


class Verbatim(str):
    """A command token to take exactly as written, quotes and all.

    pcons quotes each command token for the shell it is writing for, so
    quoting one by hand normally means it arrives at the program with the
    quotes still attached — almost always a mistake, and ``env.Command``
    raises on a token that starts with a quote.

    Shell quoting is not what this is for: a token that merely contains
    spaces or shell syntax needs no quotes of its own, because pcons adds
    them. ``["awk", "{print $$1}", "$SOURCE"]`` is the awk program spelled
    correctly; wrapping it in ``Verbatim("'{print $1}'")`` hands awk the
    single quotes as program text and it refuses to run.

    ``Verbatim`` is for the rarer case where the quotes are part of the value
    the program must receive — a defined string macro, say, where the quotes
    are C syntax rather than shell syntax:

        env.Command(..., command=["cc", Verbatim('-DGREETING="hi"'), "$SOURCE"])

    It is an ordinary ``str`` everywhere else, so substitution, quoting and
    the generators treat it exactly as they would the same text unwrapped.
    """

    __slots__ = ()


# =============================================================================
# PathToken for marking path-containing command tokens
# =============================================================================


@dataclass(frozen=True)
class PathToken:
    """A command token containing a path that needs generator-specific relativization.

    Marks path-containing tokens (include dirs, library paths, ...) so
    generators can relativize them without parsing flag prefixes.

    Attributes:
        prefix: The flag prefix (e.g., "-I", "-L", "/LIBPATH:").
        path: The path value (relative to project root or absolute).
        path_type: Type of path for relativization:
            - "project": Relative to project root (use $topdir in ninja)
            - "build": Relative to build directory (use "." in ninja)
            - "absolute": Leave unchanged
        suffix: Optional suffix to append after the path (e.g., ".d" for depfiles).

    Example:
        # Context creates:
        token = PathToken("-I", "src/include", "project")

        # Generator relativizes:
        def ninja_relativize(path):
            return f"$topdir/{path}"
        result = token.relativize(ninja_relativize)  # "-I$topdir/src/include"

        # With suffix (for depfiles):
        depfile = PathToken("", "build/obj/hello.o", "build", ".d")
        result = depfile.relativize(lambda p: p)  # "build/obj/hello.o.d"
    """

    prefix: str = ""
    path: str = ""
    path_type: str = "project"  # "project", "build", or "absolute"
    suffix: str = ""

    def relativize(self, relativizer: Callable[[str], str]) -> str:
        """Return the complete token: prefix + path + suffix, with the
        relativizer applied only to "project" paths (e.g. prepending $topdir
        for ninja); "build" and "absolute" paths pass through unchanged.
        """
        if self.path_type in ("build", "absolute"):
            return self.prefix + self.path + self.suffix
        return self.prefix + relativizer(self.path) + self.suffix

    def __str__(self) -> str:
        """Fallback string representation (no relativization)."""
        return self.prefix + self.path + self.suffix


@dataclass
class ProjectPath:
    """Marker for a path relative to project root; the prefix() function
    converts it to a PathToken with path_type="project".
    """

    path: str


@dataclass
class BuildPath:
    """Marker for a path relative to the build directory; the prefix()
    function converts it to a PathToken with path_type="build".
    """

    path: str


@dataclass(frozen=True)
class TargetPath:
    """Marker for the target output path, converted to a PathToken with the
    actual path during resolution.

    Attributes:
        index: Which target file.  ``None`` (default) means "the" target —
            maps to ``$out`` in Ninja.  An explicit integer (even 0) requests
            indexed access — ``TargetPath(index=0)`` maps to ``$target_0``,
            which is needed for multi-output builds where ``$out`` would
            expand to *all* outputs.
        suffix: Suffix to append (e.g., ".d" for depfiles).
        prefix: Optional prefix (e.g., "-MF" for MSVC-style depfile flags).
        basename: Render only the output's filename, dropping its directory —
            what a macOS install name or an ELF SONAME wants. Ninja gets a
            per-edge variable, so the rule text stays the same for every
            target and they share one rule.

    Example:
        # In toolchain's get_source_handler():
        SourceHandler("cc", "c", ".o", TargetPath(suffix=".d"), "gcc")

        # During resolution, for target "build/obj/hello.o":
        # TargetPath(suffix=".d") -> PathToken("", "build/obj/hello.o", "build", ".d")
    """

    index: int | None = None
    suffix: str = ""
    prefix: str = ""
    #: Half-open range of targets, for ${TARGETS[n:m]}. Mutually exclusive
    #: with *index*; both None means "all of them".
    start: int | None = None
    stop: int | None = None
    #: Just the filename. Mutually exclusive with *index* and a slice: those
    #: pick *which* output, and a basename marker names the edge's own.
    basename: bool = False

    @property
    def is_slice(self) -> bool:
        """True if this marker selects a range rather than one target."""
        return self.start is not None or self.stop is not None


@dataclass(frozen=True)
class NodeVar:
    """Marker for a per-edge value the toolchain attached to one node.

    A toolchain puts target-specific values in ``_build_info["vars"]`` — a
    Swift module's name, a Qt resource's name — and names them ``$MODULE_NAME``
    in its command template. Expanding those into the command text would give
    every module a rule of its own, since a ninja rule is identified by that
    text; this marker keeps the reference in the rule and lets the generator
    decide. Ninja writes the value on the build statement, so one rule serves
    every target. Generators without per-edge variables substitute the value.

    Attributes:
        name: The key in ``_build_info["vars"]``.
    """

    name: str


@dataclass(frozen=True)
class SourcePath:
    """Marker for a source input path, converted to a PathToken with the
    actual path during resolution.

    Attributes:
        index: Which source file.  ``None`` (default) means "the" source —
            maps to ``$in`` in Ninja.  An explicit integer requests indexed
            access (``$source_0``, ``$source_1``, etc.).
        suffix: Optional suffix to append.
        prefix: Optional prefix.

    Example:
        # In a command template:
        ["gcc", "-c", SourcePath(), "-o", TargetPath()]

        # During resolution:
        # SourcePath() -> PathToken("", "src/hello.c", "project")
    """

    index: int | None = None
    suffix: str = ""
    prefix: str = ""
    #: Half-open range of sources, for ${SOURCES[n:m]}. Mutually exclusive
    #: with *index*; both None means "all of them" ($in).
    start: int | None = None
    stop: int | None = None

    @property
    def is_slice(self) -> bool:
        """True if this marker selects a range rather than one source."""
        return self.start is not None or self.stop is not None


# Type alias for command tokens (can be string, PathToken, or marker objects)
# SourcePath/TargetPath markers are preserved through subst() for generators to handle
CommandToken = str | PathToken | SourcePath | TargetPath | NodeVar

# A flag, which may carry a path (PathToken) or something the generator
# resolves per edge (TargetPath, e.g. an install name naming its own output).
FlagToken = str | PathToken | TargetPath


# =============================================================================
# Namespace for variable lookup
# =============================================================================


class Namespace:
    """Hierarchical namespace for variable lookup with dotted notation."""

    def __init__(
        self,
        data: dict[str, Any] | None = None,
        parent: Namespace | None = None,
    ) -> None:
        self._data: dict[str, Any] = data.copy() if data else {}
        self._parent = parent

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self._resolve(key)
        except KeyError:
            if self._parent:
                return self._parent.get(key, default)
            return default

    def _resolve(self, key: str) -> Any:
        if "." in key:
            parts = key.split(".", 1)
            sub = self._data.get(parts[0])
            if sub is None:
                raise KeyError(key)
            if isinstance(sub, Namespace):
                return sub._resolve(parts[1])
            if isinstance(sub, dict):
                return Namespace(sub)._resolve(parts[1])
            raise KeyError(key)
        if key in self._data:
            return self._data[key]
        raise KeyError(key)

    def __contains__(self, key: str) -> bool:
        return self.get(key, _MISSING) is not _MISSING

    def __setitem__(self, key: str, value: Any) -> None:
        if "." in key:
            parts = key.split(".", 1)
            if parts[0] not in self._data:
                self._data[parts[0]] = Namespace()
            sub = self._data[parts[0]]
            if isinstance(sub, Namespace):
                sub[parts[1]] = value
            elif isinstance(sub, dict):
                sub[parts[1]] = value
            else:
                raise TypeError(f"Cannot set {key}: {parts[0]} is not a namespace")
        else:
            self._data[key] = value

    def __getitem__(self, key: str) -> Any:
        value = self.get(key, _MISSING)
        if value is _MISSING:
            raise KeyError(key)
        return value

    def update(self, other: Mapping[str, Any]) -> None:
        for key, value in other.items():
            self[key] = value


_MISSING = object()

# Sentinel character to represent literal $ during expansion (replaced at the end)
_DOLLAR_SENTINEL = "\x00"


# =============================================================================
# Pattern matching
# =============================================================================

# Match: $$, ${func(args)}, ${var}, $var
_TOKEN_PATTERN = re.compile(
    r"(\$\$)"  # Group 1: Escaped dollar
    r"|"
    r"\$\{(\w+)\(([^)]*)\)\}"  # Group 2,3: Function ${func(args)}
    r"|"
    r"\$\{([a-zA-Z_][a-zA-Z0-9_.]*)\}"  # Group 4: Braced ${var}
    r"|"
    r"\$([a-zA-Z_][a-zA-Z0-9_.]*)"  # Group 5: Simple $var
)

_ARG_SPLIT = re.compile(r",\s*")

#: The variables a generator defines in a ninja file: $in/$out, the source
#: root, and the per-edge indexed paths. A "$name" outside this set is a
#: literal dollar in the command, not a variable ninja will expand.
# Variables the generator defines, so a $ in front of one is a reference and
# every other $ is a literal dollar. Longest first: the alternation must not
# settle for "out" and leave "_basename" behind.
_NINJA_VAR_NAMES = ("out_basename", "in", "out", "topdir", r"source_\d+", r"target_\d+")
_NINJA_VARS = "(?:" + "|".join(_NINJA_VAR_NAMES) + ")"


def _ninja_var_pattern(extra: frozenset[str]) -> str:
    """The variable alternation, plus any this edge defines itself."""
    if not extra:
        return _NINJA_VARS
    names = sorted((re.escape(v) for v in extra), key=len, reverse=True)
    return "(?:" + "|".join([*names, *_NINJA_VAR_NAMES]) + ")"


def _split_template_string(template: str) -> list[str]:
    """Split a string command template on whitespace into tokens.

    Like str.split(), but treats a ``${...}`` span as a single unbreakable
    token even if it contains whitespace. This keeps function-call syntax
    such as ``${join(sep, items)}`` intact when the argument list has a
    space after the comma, instead of tearing it apart before it can be
    recognized as a function call.
    """
    tokens: list[str] = []
    current: list[str] = []
    depth = 0
    i = 0
    n = len(template)
    while i < n:
        ch = template[i]
        if ch == "$" and i + 1 < n and template[i + 1] == "{":
            depth += 1
            current.append("${")
            i += 2
            continue
        if ch == "}" and depth > 0:
            depth -= 1
            current.append("}")
            i += 1
            continue
        if ch.isspace() and depth == 0:
            if current:
                tokens.append("".join(current))
                current = []
        else:
            current.append(ch)
        i += 1
    if current:
        tokens.append("".join(current))
    return tokens


# =============================================================================
# Core substitution
# =============================================================================


def subst(
    template: str | list | MultiCmd,
    namespace: Namespace | dict[str, Any],
    *,
    location: SourceLocation | None = None,
) -> list[CommandToken] | list[list[CommandToken]]:
    """Expand variables in a template, returning structured token list.

    Args:
        template: String, list of tokens, or MultiCmd
        namespace: Variables to substitute
        location: Source location for error messages

    Returns:
        Single command: list[CommandToken] - flat list of tokens (str or PathToken)
        MultiCmd: list[list[CommandToken]] - list of commands, each a token list

    PathToken objects are created when path markers (ProjectPath, BuildPath)
    are used with the prefix() function. Generators should process these
    with appropriate relativization before converting to shell commands.
    """
    # Convert dict to Namespace if needed
    ns = namespace if isinstance(namespace, Namespace) else Namespace(namespace)

    if isinstance(template, MultiCmd):
        return [_subst_command(cmd, ns, location) for cmd in template.commands]
    return _subst_command(template, ns, location)


def _subst_command(
    template: str | list,
    namespace: Namespace,
    location: SourceLocation | None,
) -> list[CommandToken]:
    """Substitute a single command template, returning a token list.

    SourcePath/TargetPath/PathToken markers in the template are preserved
    as-is for generators to handle.
    """
    tokens = (
        _split_template_string(template)
        if isinstance(template, str)
        else list(template)
    )

    result: list[CommandToken] = []
    for token in tokens:
        # Preserve marker objects through substitution
        if isinstance(token, (SourcePath, TargetPath, PathToken, NodeVar)):
            result.append(token)
        elif isinstance(token, str):
            expanded = _expand_token(token, namespace, set(), location)
            if isinstance(expanded, list):
                # expanded is list[CommandToken] here
                result.extend(cast(list[CommandToken], expanded))
            else:
                result.append(expanded)
        else:
            # Unknown type - convert to string
            result.append(str(token))

    return result


def _expand_token(
    token: str,
    namespace: Namespace,
    expanding: set[str],
    location: SourceLocation | None,
) -> CommandToken | list[CommandToken]:
    """Expand a single token. Returns string/PathToken or list if token expands to multiple."""
    stripped = token.strip()

    # Check for function call: ${func(args)}
    func_match = re.fullmatch(r"\$\{(\w+)\(([^)]*)\)\}", stripped)
    if func_match:
        return _call_function(
            func_match.group(1), func_match.group(2), namespace, expanding, location
        )

    # Check for single variable reference (entire token)
    var_match = re.fullmatch(
        r"\$\{([a-zA-Z_][a-zA-Z0-9_.]*)\}|\$([a-zA-Z_][a-zA-Z0-9_.]*)", stripped
    )
    if var_match:
        var_name = var_match.group(1) or var_match.group(2)
        value = _lookup_var(var_name, namespace, expanding, location)

        if isinstance(value, list):
            # List variable as entire token -> multiple tokens
            var_result: list[CommandToken] = []
            for v in value:
                # Preserve marker objects through substitution
                if isinstance(v, (PathToken, SourcePath, TargetPath, NodeVar)):
                    var_result.append(v)
                elif isinstance(v, (ProjectPath, BuildPath)):
                    var_result.append(str(v))
                else:
                    sv = str(v)
                    if "$" in sv:
                        exp = _expand_token(
                            sv, namespace, expanding | {var_name}, location
                        )
                        if isinstance(exp, list):
                            var_result.extend(cast(list[CommandToken], exp))
                        else:
                            var_result.append(exp)
                    else:
                        var_result.append(sv)
            return var_result

        # Preserve marker objects (SourcePath, TargetPath, PathToken) directly
        if isinstance(value, (PathToken, SourcePath, TargetPath, NodeVar)):
            return value

        str_value = str(value)
        if "$" in str_value:
            return _expand_token(str_value, namespace, expanding | {var_name}, location)
        return str_value

    # Token contains mixed content - expand inline
    def replace_match(match: re.Match[str]) -> str:
        if match.group(1):  # $$
            # Use sentinel to protect literal $ from further expansion
            return _DOLLAR_SENTINEL

        if match.group(2):  # Function call
            func_result = _call_function(
                match.group(2), match.group(3), namespace, expanding, location
            )
            return (
                " ".join(str(x) for x in func_result)
                if isinstance(func_result, list)
                else str(func_result)
            )

        var_name = match.group(4) or match.group(5)
        value = _lookup_var(var_name, namespace, expanding, location)

        if isinstance(value, list):
            raise SubstitutionError(
                f"List variable ${var_name} cannot be embedded in '{token}'. "
                f"Use ${{prefix(...)}} or make it the entire token.",
                location,
            )
        return str(value)

    subst_result: str = _TOKEN_PATTERN.sub(replace_match, token)
    final_result: CommandToken | list[CommandToken] = subst_result

    if "$" in subst_result and subst_result != token:
        final_result = _expand_token(subst_result, namespace, expanding, location)

    # Replace sentinel with actual $ at the end
    if isinstance(final_result, str):
        final_result = final_result.replace(_DOLLAR_SENTINEL, "$")
    elif isinstance(final_result, list):
        # Process list, replacing sentinel in string tokens
        processed: list[CommandToken] = []
        for s in final_result:
            if isinstance(s, str):
                processed.append(s.replace(_DOLLAR_SENTINEL, "$"))
            else:
                # s is PathToken here
                processed.append(cast(PathToken, s))
        final_result = processed

    return final_result


def _lookup_var(
    var_name: str,
    namespace: Namespace,
    expanding: set[str],
    location: SourceLocation | None,
) -> Any:
    """Look up variable, checking for cycles."""
    if var_name in expanding:
        trace(
            "subst", "  CYCLE DETECTED: %s", " -> ".join(expanding) + " -> " + var_name
        )
        raise CircularReferenceError(list(expanding) + [var_name], location)

    value = namespace.get(var_name, _MISSING)
    if value is _MISSING:
        trace("subst", "  Variable not found: %s", var_name)
        raise MissingVariableError(var_name, location)

    if is_enabled("subst"):
        # Truncate long values for readability
        val_str = str(value)
        if len(val_str) > 80:
            val_str = val_str[:77] + "..."
        trace("subst", "  Lookup %s = %s", var_name, val_str)

    return value


def _call_function(
    func_name: str,
    args_str: str,
    namespace: Namespace,
    expanding: set[str],
    location: SourceLocation | None,
) -> list[CommandToken]:
    """Call a substitution function. Always returns a list.

    Returns list of CommandToken (str or PathToken). PathToken is returned
    when the input contains ProjectPath or BuildPath markers, allowing
    generators to apply appropriate path relativization.
    """
    args = [a.strip() for a in _ARG_SPLIT.split(args_str) if a.strip()]
    trace("subst", "  Function call: %s(%s)", func_name, args_str)

    if func_name == "prefix":
        if len(args) != 2:
            raise SubstitutionError(
                f"prefix() requires 2 args, got {len(args)}", location
            )
        prefix = str(_resolve_arg(args[0], namespace, expanding, location))
        items = _resolve_arg(args[1], namespace, expanding, location)
        items = items if isinstance(items, list) else [items]
        items = _expand_items(items, namespace, expanding, location)
        result: list[CommandToken] = []
        for item in items:
            if isinstance(item, ProjectPath):
                result.append(PathToken(prefix, item.path, "project"))
            elif isinstance(item, BuildPath):
                result.append(PathToken(prefix, item.path, "build"))
            else:
                result.append(prefix + str(item))
        return result

    elif func_name == "suffix":
        if len(args) != 2:
            raise SubstitutionError(
                f"suffix() requires 2 args, got {len(args)}", location
            )
        items = _resolve_arg(args[0], namespace, expanding, location)
        suffix = str(_resolve_arg(args[1], namespace, expanding, location))
        items = items if isinstance(items, list) else [items]
        items = _expand_items(items, namespace, expanding, location)
        suffix_result: list[CommandToken] = [str(item) + suffix for item in items]
        return suffix_result

    elif func_name == "wrap":
        if len(args) != 3:
            raise SubstitutionError(
                f"wrap() requires 3 args, got {len(args)}", location
            )
        prefix = str(_resolve_arg(args[0], namespace, expanding, location))
        items = _resolve_arg(args[1], namespace, expanding, location)
        suffix = str(_resolve_arg(args[2], namespace, expanding, location))
        items = items if isinstance(items, list) else [items]
        items = _expand_items(items, namespace, expanding, location)
        wrap_result: list[CommandToken] = [
            prefix + str(item) + suffix for item in items
        ]
        return wrap_result

    elif func_name == "join":
        if len(args) != 2:
            raise SubstitutionError(
                f"join() requires 2 args, got {len(args)}", location
            )
        sep = str(_resolve_arg(args[0], namespace, expanding, location))
        items = _resolve_arg(args[1], namespace, expanding, location)
        items = items if isinstance(items, list) else [items]
        items = _expand_items(items, namespace, expanding, location)
        join_result: list[CommandToken] = [sep.join(str(item) for item in items)]
        return join_result

    elif func_name == "pairwise":
        # Produces pairs: pairwise("-framework", ["A", "B"]) -> ["-framework", "A", "-framework", "B"]
        # Useful for linker flags like -framework Foundation -framework CoreFoundation
        if len(args) != 2:
            raise SubstitutionError(
                f"pairwise() requires 2 args, got {len(args)}", location
            )
        prefix = str(_resolve_arg(args[0], namespace, expanding, location))
        items = _resolve_arg(args[1], namespace, expanding, location)
        items = items if isinstance(items, list) else [items]
        items = _expand_items(items, namespace, expanding, location)
        pairwise_result: list[CommandToken] = []
        for item in items:
            pairwise_result.append(prefix)
            pairwise_result.append(str(item))
        return pairwise_result

    else:
        raise SubstitutionError(f"Unknown function: {func_name}", location)


def _expand_list_item(
    item: Any,
    namespace: Namespace,
    expanding: set[str],
    location: SourceLocation | None,
) -> list[Any]:
    """Recursively expand $var references embedded in a resolved list item.

    Mirrors the expansion applied to whole-token list variables in
    _expand_token(), so that list items which are themselves variable
    references (e.g. a list value of ["$other", "literal"]) get expanded
    rather than leaking a literal "$other" into the command. Non-string
    items (marker objects like ProjectPath/PathToken) pass through unchanged.
    """
    if isinstance(item, str) and "$" in item:
        expanded = _expand_token(item, namespace, expanding, location)
        return expanded if isinstance(expanded, list) else [expanded]
    return [item]


def _expand_items(
    items: list[Any],
    namespace: Namespace,
    expanding: set[str],
    location: SourceLocation | None,
) -> list[Any]:
    """Expand $var references in each element of a resolved argument list."""
    result: list[Any] = []
    for item in items:
        result.extend(_expand_list_item(item, namespace, expanding, location))
    return result


def _resolve_arg(
    arg: str,
    namespace: Namespace,
    expanding: set[str],
    location: SourceLocation | None,
) -> Any:
    """Resolve function argument - variable reference or literal."""
    if arg.startswith("${") and arg.endswith("}"):
        return _lookup_var(arg[2:-1], namespace, expanding, location)
    if arg.startswith("$"):
        return _lookup_var(arg[1:], namespace, expanding, location)

    # Dotted name = implicit variable reference
    if re.match(r"^[a-zA-Z_][a-zA-Z0-9_.]*$", arg) and "." in arg:
        return _lookup_var(arg, namespace, expanding, location)

    # Simple name - check if it's a variable
    if re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", arg):
        value = namespace.get(arg, _MISSING)
        if value is not _MISSING:
            return value

    return arg  # Literal


# =============================================================================
# Shell command formatting
# =============================================================================


def to_shell_command(
    tokens: Sequence[CommandToken] | Sequence[Sequence[CommandToken]],
    shell: str = "auto",
    multi_join: str = " && ",
    extra_ninja_vars: frozenset[str] = frozenset(),
) -> str:
    """Convert token list to shell command string with proper quoting.

    Args:
        tokens: From subst() - single command or list of commands.
                Can contain PathToken objects which will be converted via str().
        shell: "auto", "bash", "cmd", "powershell", or "ninja"
        multi_join: Separator for multiple commands

    Note: Generators should process PathToken objects with their relativizer
    before calling this function. If PathToken objects remain, they are
    converted via str() (prefix + path, no relativization).
    """
    if shell == "auto":
        shell = "cmd" if platform.system() == "Windows" else "bash"

    # Multiple commands? Check if first element is a sequence (list)
    # but not a string or PathToken
    if tokens and isinstance(tokens[0], (list, tuple)):
        # tokens is Sequence[Sequence[CommandToken]] - multiple commands
        commands = []
        for cmd_tokens in tokens:
            # cmd_tokens is a Sequence[CommandToken]
            if isinstance(cmd_tokens, (list, tuple)):
                flat_tokens = _flatten(list(cmd_tokens))
                quoted = [
                    _quote_for_shell(t, shell, extra_ninja_vars) for t in flat_tokens
                ]
                commands.append(" ".join(quoted))
        return multi_join.join(commands)
    else:
        # tokens is Sequence[CommandToken] - single command
        flat_tokens = _flatten(list(tokens))
        quoted = [_quote_for_shell(t, shell, extra_ninja_vars) for t in flat_tokens]
        return " ".join(quoted)


def _flatten(items: list) -> list[str]:
    """Flatten nested lists to flat list of strings."""
    result: list[str] = []
    for item in items:
        if isinstance(item, list):
            result.extend(_flatten(item))
        else:
            result.append(str(item))
    return result


#: Tokens that are shell syntax rather than arguments. Quoting one turns
#: redirection or a pipeline into a literal argument, so the command silently
#: does something else -- `tool in > out` writes nothing and passes ">" and
#: "out" to the tool. A command template that wants a literal ">" has to quote
#: it itself; a bare one, after tokenization, can only mean the operator.
_SHELL_OPERATORS = frozenset(
    {
        ">",
        ">>",
        "<",
        "<<",
        "|",
        "||",
        "&&",
        "&",
        ";",
        "2>",
        "2>&1",
        ">&2",
        "2>>",
    }
)


def _quote_for_shell(
    s: str, shell: str, extra_ninja_vars: frozenset[str] = frozenset()
) -> str:
    """Quote string for target shell if needed.

    Args:
        s: String to quote
        shell: Target shell ("bash", "cmd", "powershell", or "ninja")

    For "ninja" shell, ninja variables like $in, $out are not quoted,
    but other arguments with spaces are quoted for shell execution.
    """
    if not s:
        return "''" if shell not in ("cmd", "ninja") else '""' if shell == "cmd" else ""

    if s in _SHELL_OPERATORS:
        return s

    if shell == "ninja":
        # Ninja commands are passed to the shell (sh on Unix, cmd on Windows).
        # Use double quotes for cross-platform compatibility.
        #
        # IMPORTANT: Ninja's $in/$out variables expand to space-separated file lists.
        # We CANNOT quote them because that would make multiple files into one argument.
        # Paths with spaces in multi-file commands are a known limitation.
        #
        # Strategy:
        # - Ninja variables ($in, $out, $topdir, etc.) → don't quote (ninja expands them)
        # - Shell operators (>, |, &&) → don't quote (handled above, for every shell)
        # - Tokens containing shell metacharacters (spaces, but also things like
        #   backticks, ;, |, &, (, ), <, >, *, ? - which may come from
        #   attacker-influenced input such as globbed filenames or flags pulled
        #   from a dependency's Conan/pkg-config metadata) → quote with double
        #   quotes so the shell can't interpret them
        # - Simple flags (--type, -c) → don't quote
        # Ninja variables - don't quote, ninja will expand them (and $in/$out
        # may expand to several space-separated paths). Only the variables the
        # generator actually defines qualify -- $in, $out, $topdir, $out.d,
        # $source_N: any other $name is a literal dollar in the command and
        # must be escaped like one below.
        ninja_vars = _ninja_var_pattern(extra_ninja_vars)
        if re.fullmatch(rf"\${ninja_vars}(?:\.\w+)*", s):
            return s

        # $in and $out are the two variables ninja escapes for the shell
        # itself, so a token carrying one must not be quoted here as well: our
        # quotes would nest inside ninja's and the path would split at its
        # spaces. `/Fo$out` is the case that bites, since a prefixed output
        # flag puts the variable inside a larger token.
        ninja_escapes_the_path = re.search(r"\$(?:in|out)\b", s) is not None

        # Any shell metacharacter (not just whitespace) means the shell could
        # interpret this token specially: backticks/`$(...)` (command
        # substitution), `;`/`|`/`&` (command separators), `()`/`<>` etc.
        # Decide on quoting - and escape backslash/quote/backtick for the
        # quoted form - using the token's original content, before the $
        # escaping below inserts backslashes of its own.
        needs_quote = not ninja_escapes_the_path and any(
            c in s for c in " \t\n\"'\\$`!*?[](){}|&;<>#"
        )
        if needs_quote:
            if platform.system() == "Windows":
                # Ninja runs cmd.exe on Windows. Backslash is not an escape
                # character there, and backtick / $(...) are not command
                # substitution, so wrapping in double quotes already
                # neutralizes cmd's metacharacters (& | < > ^). Only embedded
                # double quotes need escaping (\" for CommandLineToArgvW).
                # Escaping backslashes here would corrupt payloads that rely on
                # them, e.g. `python -c` string escapes or Windows paths.
                s = s.replace('"', '\\"')
            else:
                # Ninja runs /bin/sh. Escape backslash, double quote, and
                # backtick so the token survives intact inside a double-quoted
                # shell string and can't break out or trigger command
                # substitution.
                s = s.replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`")

        # Escape literal $ signs that are NOT ninja variable references, so a
        # dollar the user wrote reaches the command verbatim.
        # On Unix it has to survive both layers:
        #   \$$ in ninja file → ninja expands $$ to $ → shell sees \$ → literal $
        # Windows has no shell layer to hide from (ninja calls CreateProcess,
        # and even cmd.exe leaves $ alone), so there ninja's own doubling is
        # the whole job; a backslash would reach the program as one.
        # This handles e.g. -Wl,-rpath,$ORIGIN (linker token, not a variable).
        # Every other dollar is literal, including one that follows another:
        # exempting "$$" here would emit "$\$$", which ninja rejects outright
        # as a bad $-escape.
        escaped_dollar = "$$" if platform.system() == "Windows" else "\\$$"
        s = re.sub(
            rf"\$(?!({ninja_vars})(?!\w))",
            lambda m: escaped_dollar,
            s,
        )

        return f'"{s}"' if needs_quote else s

    if shell == "bash":
        needs_quote = any(c in s for c in " \t\n\"'\\$`!*?[](){}|&;<>#")
        if not needs_quote:
            return s
        if "'" not in s:
            return f"'{s}'"
        escaped = (
            s.replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("$", "\\$")
            .replace("`", "\\`")
        )
        return f'"{escaped}"'

    elif shell == "cmd":
        needs_quote = any(c in s for c in ' \t"^&|<>()%!')
        if not needs_quote:
            return s
        return f'"{s.replace(chr(34), chr(34) + chr(34))}"'

    elif shell == "powershell":
        needs_quote = any(c in s for c in " \t\"'$`(){}[]|&;<>#")
        if not needs_quote:
            return s
        if "'" not in s:
            return f"'{s}'"
        return f"'{s.replace(chr(39), chr(39) + chr(39))}'"

    return f'"{s}"' if " " in s else s
