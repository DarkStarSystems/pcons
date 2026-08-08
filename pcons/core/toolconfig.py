# SPDX-License-Identifier: MIT
"""Tool configuration namespace.

ToolConfig provides a namespace for a single tool's configuration variables.
It supports attribute-style access (env.cc.flags) and integrates with the
variable substitution system.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pcons.core._toolconfig_stubs import _ToolConfigStubs
else:
    # At runtime, ToolConfig inherits from `object`. The mixin only provides
    # typed declarations for static analysis. __getattr__/__setattr__ continue
    # to dispatch dynamic variables as before.
    _ToolConfigStubs = object


class ToolConfig(_ToolConfigStubs):
    """Configuration namespace for a single tool.

    Provides attribute-style access to tool variables:
        tool.cmd = 'gcc'
        tool.flags = ['-Wall', '-O2']

    Variables can be accessed as a namespace dict for substitution:
        subst('$cc.cmd $cc.flags', {'cc': tool.as_namespace()})

    Attributes:
        name: The tool's name (e.g., 'cc', 'cxx', 'link').
    """

    __slots__ = ("_name", "_vars", "_env", "_declared")

    def __init__(self, name: str, **defaults: Any) -> None:
        """Create a tool configuration.

        Args:
            name: The tool's name.
            **defaults: Default variable values.
        """
        object.__setattr__(self, "_name", name)
        # Every tool that runs a command can be run behind one (ccache, a
        # profiler, a persistent-worker client), so the variable is always
        # there rather than depending on the tool's author to declare it.
        object.__setattr__(self, "_vars", {"launcher": [], **defaults})
        # Back-reference to the owning Environment, set when the tool is added.
        # Used only by explain(); None for detached/standalone ToolConfigs.
        object.__setattr__(self, "_env", None)
        # True once the owning Tool has declared the variables it consumes;
        # see mark_declared().
        object.__setattr__(self, "_declared", False)

    @property
    def name(self) -> str:
        """The tool's name."""
        name: str = object.__getattribute__(self, "_name")
        return name

    def __getattr__(self, name: str) -> Any:
        """Get a tool variable, or a toolchain-provided setting method.

        Domain settings (e.g. ``env.cxx.set_standard``) are not stored as
        variables; they're resolved through the owning environment's toolchain
        at access time, so they stay tool-agnostic in core and clone-safe. See
        docs/presets.md.
        """
        if name.startswith("_"):
            raise AttributeError(f"'{type(self).__name__}' has no attribute '{name}'")
        vars_dict = object.__getattribute__(self, "_vars")
        if name in vars_dict:
            return vars_dict[name]

        env = object.__getattribute__(self, "_env")
        if env is not None:
            tool_name = object.__getattribute__(self, "_name")
            for toolchain in env.toolchains:
                setting = toolchain.tool_setting(tool_name, name)
                if setting is not None:
                    return lambda *args, **kwargs: setting(env, *args, **kwargs)

        raise AttributeError(
            f"Tool '{self.name}' has no variable '{name}'. "
            f"Available: {', '.join(vars_dict.keys()) or '(none)'}"
        )

    # Variable names that should always be lists (flags, paths, etc.)
    # Command templates (objcmd, linkcmd, etc.) can be strings or lists.
    _LIST_ONLY_VARS = frozenset({"flags", "includes", "defines", "libs", "launcher"})

    def mark_declared(self) -> None:
        """Note that the owning tool has declared the variables it consumes.

        From here on an unknown name in an attribute assignment is a typo
        (``env.cxx.cxxflags = [...]``) and raises. Namespaces nobody declared
        stay open: with no known-names list there is nothing to check against.
        """
        object.__setattr__(self, "_declared", True)

    def __setattr__(self, name: str, value: Any) -> None:
        """Set an existing tool variable.

        Once the tool has declared its variables (:meth:`mark_declared`), a
        name it doesn't have is a typo: it would be stored, read by nothing,
        and leave the build silently unflagged. Introducing a genuinely new
        variable is spelled :meth:`set`.

        Raises:
            AttributeError: If this tool declared its variables and has none
                      by that name.
            TypeError: If assigning a string to a known list-type variable
                      (likely a user error like ``env.cc.flags = "-Wall"``
                      instead of ``env.cc.flags = ["-Wall"]``).
        """
        if name.startswith("_"):
            object.__setattr__(self, name, value)
        else:
            vars_dict = object.__getattribute__(self, "_vars")
            if name not in vars_dict:
                if object.__getattribute__(self, "_declared"):
                    raise AttributeError(self._unknown_variable_message(name))
                vars_dict[name] = value
                return
            # Catch common mistake: assigning string to a list-only variable
            if (
                isinstance(value, str)
                and name in self._LIST_ONLY_VARS
                and isinstance(vars_dict[name], list)
            ):
                raise TypeError(
                    f"Cannot assign a string to '{self.name}.{name}' "
                    f"(must be a list). "
                    f'Use {self.name}.{name} = ["{value}"] or '
                    f'{self.name}.{name}.append("{value}").'
                )
            vars_dict[name] = value

    def _unknown_variable_message(self, name: str) -> str:
        """Explain an unrecognized variable name, and guess the intent."""
        import difflib

        vars_dict = object.__getattribute__(self, "_vars")
        known = sorted(k for k in vars_dict if not k[:1].isupper())
        message = (
            f"Tool '{self.name}' has no variable '{name}', so setting it would "
            f"have no effect on the build."
        )
        close = difflib.get_close_matches(name, known, n=1, cutoff=0.6)
        if close:
            message += f" Did you mean '{self.name}.{close[0]}'?"
        return message + (
            f"\n  Known variables: {', '.join(known) or '(none)'}."
            f"\n  To introduce a new one (for a custom command line), declare "
            f"it: {self.name}.set({name!r}, ...)."
        )

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
        """Set a variable, declaring it if the tool doesn't have it yet.

        Attribute assignment (``tool.flags = [...]``) only updates variables
        the tool already has, so this is how a tool or an extension
        introduces one.
        """
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

    def explain(self) -> Any:
        """Explain where this tool's flags came from (see Environment.explain).

        Returns an :class:`~pcons.core.explain.Explanation` for this tool.
        """
        env = object.__getattribute__(self, "_env")
        if env is None:
            raise RuntimeError(
                f"Tool '{self.name}' is not attached to an environment; "
                f"use env.explain('{self.name}') instead."
            )
        return env.explain(tool=self.name)

    def as_namespace(self) -> dict[str, Any]:
        """Return as a namespace dict for substitution.

        Lists and dicts are copied so substitution can't mutate the tool config.
        """
        vars_dict: dict[str, Any] = object.__getattribute__(self, "_vars")
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
            if isinstance(value, list):
                new_vars[key] = list(value)
            elif isinstance(value, dict):
                new_vars[key] = dict(value)
            else:
                new_vars[key] = value
        clone = ToolConfig(self.name, **new_vars)
        if object.__getattribute__(self, "_declared"):
            clone.mark_declared()
        return clone

    def __repr__(self) -> str:
        vars_dict = object.__getattribute__(self, "_vars")
        return f"ToolConfig({self.name!r}, {vars_dict!r})"
