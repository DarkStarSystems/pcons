# SPDX-License-Identifier: MIT
"""Variable substitution engine for pcons.

This module provides recursive variable expansion for building
command lines and other templated strings. It supports:

- Simple variables: $VAR or ${VAR}
- Namespaced variables: $tool.var or ${tool.var}
- Recursive expansion (expand until no $ remain)
- Circular reference detection
- List values (space-joined when interpolated into strings)
- Escaped dollars: $$ becomes literal $
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from pcons.core.errors import CircularReferenceError, MissingVariableError
from pcons.util.source_location import SourceLocation

# Pattern to match variable references:
# - $$ -> escaped dollar (group 1)
# - ${name} -> braced variable (group 2)
# - $name -> simple variable (group 3)
# Name can include dots for namespaced access (e.g., cc.flags)
_VAR_PATTERN = re.compile(
    r"(\$\$)"  # Escaped dollar
    r"|"
    r"\$\{([a-zA-Z_][a-zA-Z0-9_.]*)\}"  # Braced: ${var} or ${tool.var}
    r"|"
    r"\$([a-zA-Z_][a-zA-Z0-9_.]*)"  # Simple: $var or $tool.var
)


class Namespace:
    """A hierarchical namespace for variable lookup.

    Supports both flat access (ns['key']) and dotted access (ns['tool.key']).
    Can be nested for tool-specific namespaces.
    """

    def __init__(
        self,
        data: dict[str, Any] | None = None,
        parent: Namespace | None = None,
    ) -> None:
        """Create a namespace.

        Args:
            data: Initial data dictionary.
            parent: Parent namespace for fallback lookups.
        """
        self._data: dict[str, Any] = data.copy() if data else {}
        self._parent = parent

    def get(self, key: str, default: Any = None) -> Any:
        """Get a value, supporting dotted notation.

        Args:
            key: Variable name, possibly with dots (e.g., 'cc.flags').
            default: Value to return if not found.

        Returns:
            The value, or default if not found.
        """
        try:
            return self._resolve(key)
        except KeyError:
            if self._parent:
                return self._parent.get(key, default)
            return default

    def _resolve(self, key: str) -> Any:
        """Resolve a key, handling dotted notation."""
        if "." in key:
            parts = key.split(".", 1)
            sub = self._data.get(parts[0])
            if sub is None:
                raise KeyError(key)
            if isinstance(sub, Namespace):
                return sub._resolve(parts[1])
            if isinstance(sub, dict):
                # Convert dict to namespace on the fly
                return Namespace(sub)._resolve(parts[1])
            raise KeyError(key)
        if key in self._data:
            return self._data[key]
        raise KeyError(key)

    def __contains__(self, key: str) -> bool:
        """Check if a key exists."""
        return self.get(key, _MISSING) is not _MISSING

    def __setitem__(self, key: str, value: Any) -> None:
        """Set a value, supporting dotted notation for nested access."""
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
        """Get a value, raising KeyError if not found."""
        value = self.get(key, _MISSING)
        if value is _MISSING:
            raise KeyError(key)
        return value

    def update(self, other: Mapping[str, Any]) -> None:
        """Update with values from another mapping."""
        for key, value in other.items():
            self[key] = value


# Sentinel for missing values
_MISSING = object()

# Marker for escaped dollars during expansion (replaced with $ at the end)
_ESCAPED_DOLLAR_MARKER = "\x00DOLLAR\x00"


def subst(
    template: str,
    namespace: Namespace | dict[str, Any],
    *,
    location: SourceLocation | None = None,
) -> str:
    """Expand variables in a template string.

    Performs recursive expansion until no $ references remain
    (except escaped $$).

    Args:
        template: String containing $VAR or ${VAR} references.
        namespace: Variables to substitute. Can be a Namespace or dict.
        location: Source location for error messages.

    Returns:
        The expanded string.

    Raises:
        MissingVariableError: If a referenced variable doesn't exist.
        CircularReferenceError: If variables reference each other cyclically.
    """
    if isinstance(namespace, dict):
        namespace = Namespace(namespace)

    result = _expand(template, namespace, set(), location)
    # Restore escaped dollars (marker -> $) only at the top level
    result = result.replace(_ESCAPED_DOLLAR_MARKER, "$")
    return result


def subst_list(
    template: str,
    namespace: Namespace | dict[str, Any],
    *,
    location: SourceLocation | None = None,
) -> list[str]:
    """Expand variables and return as a list of strings.

    Like subst(), but splits the result on whitespace and
    handles list values by expanding them into multiple items.

    Args:
        template: String containing $VAR or ${VAR} references.
        namespace: Variables to substitute.
        location: Source location for error messages.

    Returns:
        A list of strings (template tokens with variables expanded).
    """
    if isinstance(namespace, dict):
        namespace = Namespace(namespace)

    result = _expand(template, namespace, set(), location)
    return result.split()


def _expand(
    template: str,
    namespace: Namespace,
    expanding: set[str],
    location: SourceLocation | None,
) -> str:
    """Recursively expand variables in template.

    Args:
        template: Template string.
        namespace: Variable namespace.
        expanding: Set of variables currently being expanded (for cycle detection).
        location: Source location for error messages.

    Returns:
        Expanded string.
    """

    def replace_var(match: re.Match[str]) -> str:
        # Group 1: escaped $$ -> marker (converted to $ at the end)
        if match.group(1):
            return _ESCAPED_DOLLAR_MARKER

        # Group 2 or 3: variable name
        var_name = match.group(2) or match.group(3)

        # Check for circular reference
        if var_name in expanding:
            chain = list(expanding) + [var_name]
            raise CircularReferenceError(chain, location)

        # Look up the value
        value = namespace.get(var_name, _MISSING)
        if value is _MISSING:
            raise MissingVariableError(var_name, location)

        # Convert to string
        str_value = _value_to_str(value)

        # Recursively expand if there are more $ references
        if "$" in str_value and str_value != "$":
            new_expanding = expanding | {var_name}
            return _expand(str_value, namespace, new_expanding, location)

        return str_value

    # Keep expanding until no more substitutions happen
    result = _VAR_PATTERN.sub(replace_var, template)

    # Check if we need another pass (in case expansion introduced new variables)
    # But skip if the only $ is from $$ escaping (which is now a marker)
    if "$" in result:
        # Make sure we're not in an infinite loop
        if result != template:
            return _expand(result, namespace, expanding, location)

    return result


def _value_to_str(value: Any) -> str:
    """Convert a value to a string suitable for substitution.

    Lists are space-joined. Other types are converted via str().
    """
    if isinstance(value, list):
        return " ".join(str(item) for item in value)
    if isinstance(value, bool):
        return "1" if value else "0"
    return str(value)


def escape(s: str) -> str:
    """Escape dollar signs in a string.

    Args:
        s: String to escape.

    Returns:
        String with $ replaced by $$.
    """
    return s.replace("$", "$$")
