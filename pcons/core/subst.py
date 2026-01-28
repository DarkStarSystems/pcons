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
- String: "$cc.cmd $cc.flags -c -o $$TARGET $$SOURCE" (auto-tokenized on whitespace)
- List: ["$cc.cmd", "$cc.flags", "-c", "-o", "$$TARGET", "$$SOURCE"] (explicit tokens)
- MultiCmd: MultiCmd(["cmd1 args", "cmd2 args"]) (multiple commands)

Generator-agnostic variables:
- $$SOURCE / $$SOURCES: Input file(s), converted by generators to native syntax
- $$TARGET / $$TARGETS: Output file(s), converted by generators to native syntax
- $$TARGET.d: In command templates (e.g., depflags), expanded to actual depfile path

Depfile paths in build_info use PathToken with suffix=".d" for type-safe handling.
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


# =============================================================================
# PathToken for marking path-containing command tokens
# =============================================================================


@dataclass(frozen=True)
class PathToken:
    """A command token containing a path that needs generator-specific relativization.

    This class marks tokens that contain paths (like include directories or library
    paths) so that generators can apply appropriate relativization without needing
    to parse flag prefixes with regex.

    The context creates PathToken objects for path values, and generators call
    relativize() with their path transformation function.

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
        """Apply a relativization function and return the complete token.

        Args:
            relativizer: Function that transforms the path for the target generator.
                        Receives the raw path, returns the relativized path.

        Returns:
            The complete token: prefix + relativized path + suffix.
        """
        return self.prefix + relativizer(self.path) + self.suffix

    def __str__(self) -> str:
        """Fallback string representation (no relativization)."""
        return self.prefix + self.path + self.suffix


@dataclass
class ProjectPath:
    """Marker for a path relative to project root.

    Used by contexts to mark include/lib paths that need relativization
    for the target generator. The prefix() function converts these to
    PathToken objects with path_type="project".

    Example:
        context.get_env_overrides() returns:
            {"includes": [ProjectPath("src/include"), ProjectPath("lib/headers")]}

        prefix() converts to:
            [PathToken("-I", "src/include", "project"), ...]
    """

    path: str


@dataclass
class BuildPath:
    """Marker for a path relative to build directory.

    Used for paths that are within the build output directory.
    The prefix() function converts these to PathToken with path_type="build".

    Example:
        context.get_env_overrides() returns:
            {"includes": [BuildPath("generated")]}

        prefix() converts to:
            [PathToken("-I", "generated", "build"), ...]
    """

    path: str


@dataclass(frozen=True)
class TargetPath:
    """Marker for target output path, resolved during resolve phase.

    This marker is used in SourceHandler.depfile to indicate a path derived
    from the target output. During resolution, it's converted to a PathToken
    with the actual target path.

    Attributes:
        index: Which target file (0 = first/only, for multi-output commands).
        suffix: Suffix to append (e.g., ".d" for depfiles).
        prefix: Optional prefix (e.g., "-MF" for MSVC-style depfile flags).

    Example:
        # In toolchain's get_source_handler():
        SourceHandler("cc", "c", ".o", TargetPath(suffix=".d"), "gcc")

        # During resolution, for target "build/obj/hello.o":
        # TargetPath(suffix=".d") -> PathToken("", "build/obj/hello.o", "build", ".d")
    """

    index: int = 0
    suffix: str = ""
    prefix: str = ""


@dataclass(frozen=True)
class SourcePath:
    """Marker for source input path, resolved during resolve phase.

    This marker is used in command templates to reference source files.
    During resolution, it's converted to a PathToken with the actual source path.

    Attributes:
        index: Which source file (0 = first/only, for multi-source commands).
        suffix: Optional suffix to append.
        prefix: Optional prefix.

    Example:
        # In a command template:
        ["gcc", "-c", SourcePath(), "-o", TargetPath()]

        # During resolution:
        # SourcePath() -> PathToken("", "src/hello.c", "project")
    """

    index: int = 0
    suffix: str = ""
    prefix: str = ""


# Type alias for command tokens (can be string, PathToken, or marker objects)
# SourcePath/TargetPath markers are preserved through subst() for generators to handle
CommandToken = str | PathToken | SourcePath | TargetPath


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
    else:
        return _subst_command(template, ns, location)


def _subst_command(
    template: str | list,
    namespace: Namespace,
    location: SourceLocation | None,
) -> list[CommandToken]:
    """Substitute a single command template, returning token list.

    Returns list of CommandToken (str or PathToken). PathToken objects
    are created when path markers (ProjectPath, BuildPath) are used with
    prefix() function, allowing generators to apply relativization.

    SourcePath and TargetPath marker objects in the template are preserved
    as-is, allowing generators to convert them to appropriate syntax.
    """
    tokens = template.split() if isinstance(template, str) else list(template)

    result: list[CommandToken] = []
    for token in tokens:
        # Preserve marker objects through substitution
        if isinstance(token, (SourcePath, TargetPath, PathToken)):
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
                if isinstance(v, (PathToken, SourcePath, TargetPath)):
                    var_result.append(v)
                elif isinstance(v, (ProjectPath, BuildPath)):
                    var_result.append(str(v))
                else:
                    sv = str(v)
                    if "$" in sv:
                        exp = _expand_token(sv, namespace, expanding, location)
                        if isinstance(exp, list):
                            var_result.extend(cast(list[CommandToken], exp))
                        else:
                            var_result.append(exp)
                    else:
                        var_result.append(sv)
            return var_result

        # Preserve marker objects (SourcePath, TargetPath, PathToken) directly
        if isinstance(value, (PathToken, SourcePath, TargetPath)):
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
        pairwise_result: list[CommandToken] = []
        for item in items:
            pairwise_result.append(prefix)
            pairwise_result.append(str(item))
        return pairwise_result

    else:
        raise SubstitutionError(f"Unknown function: {func_name}", location)


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
                quoted = [_quote_for_shell(t, shell) for t in flat_tokens]
                commands.append(" ".join(quoted))
        return multi_join.join(commands)
    else:
        # tokens is Sequence[CommandToken] - single command
        flat_tokens = _flatten(list(tokens))
        quoted = [_quote_for_shell(t, shell) for t in flat_tokens]
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


def _quote_for_shell(s: str, shell: str) -> str:
    """Quote string for target shell if needed.

    Args:
        s: String to quote
        shell: Target shell ("bash", "cmd", "powershell", or "ninja")

    For "ninja" shell, ninja variables like $in, $out are not quoted,
    but other arguments with spaces are quoted for shell execution.
    """
    if not s:
        return "''" if shell not in ("cmd", "ninja") else '""' if shell == "cmd" else ""

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
        # - Shell operators (>, |, &&) → don't quote
        # - Path-like arguments with spaces (pcons-expanded) → quote with double quotes
        # - Simple flags (--type, -c) → don't quote
        import re

        # Shell operators should not be quoted
        shell_operators = {
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
        if s in shell_operators:
            return s

        # Ninja variables - don't quote, ninja will expand them
        # This includes $in, $out, $topdir, $out.d, etc.
        if re.match(r"^\$[a-zA-Z_][a-zA-Z0-9_.]*$", s):
            return s

        # Path-like arguments with spaces need quoting (for paths pcons expanded)
        # Use double quotes for cross-platform compatibility
        has_spaces = " " in s or "\t" in s
        if has_spaces:
            # Escape embedded double quotes
            escaped = s.replace('"', '\\"')
            return f'"{escaped}"'

        # Everything else (flags, paths without spaces) - pass through
        return s

    if shell == "bash":
        needs_quote = any(c in s for c in " \t\n\"'\\$`!*?[](){}|&;<>")
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
        needs_quote = any(c in s for c in " \t\"'$`(){}[]|&;<>")
        if not needs_quote:
            return s
        if "'" not in s:
            return f"'{s}'"
        return f"'{s.replace(chr(39), chr(39) + chr(39))}'"

    return f'"{s}"' if " " in s else s


def escape(s: str) -> str:
    """Escape dollar signs: $ -> $$"""
    return s.replace("$", "$$")
