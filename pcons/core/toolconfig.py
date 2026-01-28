# SPDX-License-Identifier: MIT
"""Tool configuration namespace.

ToolConfig provides a namespace for a single tool's configuration variables.
It supports attribute-style access (env.cc.flags) and integrates with the
variable substitution system.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any


class ToolConfig:
    """Configuration namespace for a single tool.

    Provides attribute-style access to tool variables:
        tool.cmd = 'gcc'
        tool.flags = ['-Wall', '-O2']

    Variables can be accessed as a namespace dict for substitution:
        subst('$cc.cmd $cc.flags', {'cc': tool.as_namespace()})

    Attributes:
        name: The tool's name (e.g., 'cc', 'cxx', 'link').
    """

    __slots__ = ("_name", "_vars")

    def __init__(self, name: str, **defaults: Any) -> None:
        """Create a tool configuration.

        Args:
            name: The tool's name.
            **defaults: Default variable values.
        """
        object.__setattr__(self, "_name", name)
        object.__setattr__(self, "_vars", dict(defaults))

    @property
    def name(self) -> str:
        """The tool's name."""
        name: str = object.__getattribute__(self, "_name")
        return name

    def __getattr__(self, name: str) -> Any:
        """Get a tool variable."""
        if name.startswith("_"):
            raise AttributeError(f"'{type(self).__name__}' has no attribute '{name}'")
        vars_dict = object.__getattribute__(self, "_vars")
        if name in vars_dict:
            return vars_dict[name]
        raise AttributeError(
            f"Tool '{self.name}' has no variable '{name}'. "
            f"Available: {', '.join(vars_dict.keys()) or '(none)'}"
        )

    def __setattr__(self, name: str, value: Any) -> None:
        """Set a tool variable."""
        if name.startswith("_"):
            object.__setattr__(self, name, value)
        else:
            vars_dict = object.__getattribute__(self, "_vars")
            vars_dict[name] = value

    def __delattr__(self, name: str) -> None:
        """Delete a tool variable."""
        vars_dict = object.__getattribute__(self, "_vars")
        if name in vars_dict:
            del vars_dict[name]
        else:
            raise AttributeError(f"Tool '{self.name}' has no variable '{name}'")

    def __contains__(self, name: str) -> bool:
        """Check if a variable exists."""
        vars_dict = object.__getattribute__(self, "_vars")
        return name in vars_dict

    def __iter__(self) -> Iterator[str]:
        """Iterate over variable names."""
        vars_dict = object.__getattribute__(self, "_vars")
        return iter(vars_dict)

    def get(self, name: str, default: Any = None) -> Any:
        """Get a variable with a default."""
        vars_dict = object.__getattribute__(self, "_vars")
        return vars_dict.get(name, default)

    def set(self, name: str, value: Any) -> None:
        """Set a variable (alternative to attribute access)."""
        vars_dict = object.__getattribute__(self, "_vars")
        vars_dict[name] = value

    def update(self, values: dict[str, Any]) -> None:
        """Update multiple variables."""
        vars_dict = object.__getattribute__(self, "_vars")
        vars_dict.update(values)

    def as_dict(self) -> dict[str, Any]:
        """Return variables as a dictionary (shallow copy)."""
        vars_dict = object.__getattribute__(self, "_vars")
        return dict(vars_dict)

    def as_namespace(self) -> dict[str, Any]:
        """Return as a namespace dict for substitution.

        Returns a shallow copy of the variables dict with deep copies of
        mutable values (lists, dicts) to prevent accidental mutation of
        the original tool configuration during variable substitution.
        """
        vars_dict: dict[str, Any] = object.__getattribute__(self, "_vars")
        # Return a copy with deep-copied mutable values to prevent mutation
        result: dict[str, Any] = {}
        for key, value in vars_dict.items():
            if isinstance(value, list):
                result[key] = list(value)
            elif isinstance(value, dict):
                result[key] = dict(value)
            else:
                result[key] = value
        return result

    def clone(self) -> ToolConfig:
        """Create a deep copy of this tool configuration."""
        vars_dict: dict[str, Any] = object.__getattribute__(self, "_vars")
        new_vars: dict[str, Any] = {}
        for key, value in vars_dict.items():
            # Deep copy lists to avoid shared mutation
            if isinstance(value, list):
                new_vars[key] = list(value)
            elif isinstance(value, dict):
                new_vars[key] = dict(value)
            else:
                new_vars[key] = value
        return ToolConfig(self.name, **new_vars)

    def __repr__(self) -> str:
        vars_dict = object.__getattribute__(self, "_vars")
        return f"ToolConfig({self.name!r}, {vars_dict!r})"
