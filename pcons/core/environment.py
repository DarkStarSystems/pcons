# SPDX-License-Identifier: MIT
"""Environment with namespaced tool configuration.

An Environment holds configuration for a build, including tool-specific
namespaces (env.cc, env.cxx, etc.) and cross-tool variables.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator

from pcons.core.subst import Namespace, subst, to_shell_command
from pcons.core.toolconfig import ToolConfig
from pcons.util.source_location import SourceLocation, get_caller_location

if TYPE_CHECKING:
    from pcons.core.node import FileNode
    from pcons.tools.toolchain import Toolchain


class Environment:
    """Build environment with namespaced tool configuration.

    Provides namespaced access to tool configuration:
        env.cc.cmd = 'gcc'
        env.cc.flags = ['-Wall', '-O2']
        env.cxx.flags = ['-std=c++20']

    Cross-tool variables are accessed directly:
        env.build_dir = 'build/release'
        env.variant = 'release'

    Environments can be cloned for variant builds:
        debug = env.clone()
        debug.cc.flags += ['-g']

    Attributes:
        build_dir: Directory for build outputs.
        defined_at: Source location where this environment was created.
    """

    __slots__ = (
        "_tools",
        "_vars",
        "_project",
        "_toolchain",
        "_created_nodes",
        "defined_at",
    )

    def __init__(
        self,
        *,
        toolchain: Toolchain | None = None,
        defined_at: SourceLocation | None = None,
    ) -> None:
        """Create an environment.

        Args:
            toolchain: Optional toolchain to initialize tools from.
            defined_at: Source location where this was created.
        """
        self._tools: dict[str, ToolConfig] = {}
        self._vars: dict[str, Any] = {
            "build_dir": Path("build"),
            "variant": "default",
        }
        self._project: Any = None  # Set by Project when env is created
        self._toolchain = toolchain
        self._created_nodes: list[Any] = []  # Nodes created by builders
        self.defined_at = defined_at or get_caller_location()

        # Initialize tools from toolchain if provided
        if toolchain is not None:
            toolchain.setup(self)

    # Private helper methods to reduce object.__getattribute__ verbosity
    def _get_tools(self) -> dict[str, ToolConfig]:
        """Get the internal tools dictionary."""
        return object.__getattribute__(self, "_tools")

    def _get_vars(self) -> dict[str, Any]:
        """Get the internal variables dictionary."""
        return object.__getattribute__(self, "_vars")

    def _get_created_nodes(self) -> list[Any]:
        """Get the internal created nodes list."""
        return object.__getattribute__(self, "_created_nodes")

    def __getattr__(self, name: str) -> Any:
        """Get a tool namespace or cross-tool variable.

        Tool namespaces take precedence over variables.
        """
        if name.startswith("_"):
            raise AttributeError(f"'{type(self).__name__}' has no attribute '{name}'")

        # Check for tool namespace first
        tools = self._get_tools()
        if name in tools:
            return tools[name]

        # Check for cross-tool variable
        vars_dict = self._get_vars()
        if name in vars_dict:
            return vars_dict[name]

        raise AttributeError(
            f"Environment has no tool or variable '{name}'. "
            f"Tools: {', '.join(tools.keys()) or '(none)'}. "
            f"Vars: {', '.join(vars_dict.keys()) or '(none)'}"
        )

    def __setattr__(self, name: str, value: Any) -> None:
        """Set a cross-tool variable or replace a tool config."""
        if name.startswith("_") or name == "defined_at":
            object.__setattr__(self, name, value)
        elif isinstance(value, ToolConfig):
            tools = self._get_tools()
            tools[name] = value
        else:
            vars_dict = self._get_vars()
            vars_dict[name] = value

    def add_tool(self, name: str, config: ToolConfig | None = None) -> ToolConfig:
        """Add or get a tool namespace.

        If the tool already exists, returns it. Otherwise creates
        a new ToolConfig.

        Args:
            name: Tool name (e.g., 'cc', 'cxx').
            config: Optional existing config to use.

        Returns:
            The ToolConfig for this tool.
        """
        tools = self._get_tools()
        if name in tools:
            return tools[name]
        if config is None:
            config = ToolConfig(name)
        tools[name] = config
        return config

    def has_tool(self, name: str) -> bool:
        """Check if a tool namespace exists."""
        return name in self._get_tools()

    def tool_names(self) -> list[str]:
        """Return list of configured tool names."""
        return list(self._get_tools().keys())

    def register_node(self, node: Any) -> None:
        """Register a node created by a builder.

        This tracks nodes so the generator can find all build targets.

        Args:
            node: The node to register.
        """
        self._get_created_nodes().append(node)

    @property
    def created_nodes(self) -> list[Any]:
        """Return list of nodes created by builders in this environment."""
        return self._get_created_nodes()

    def get(self, name: str, default: Any = None) -> Any:
        """Get a variable or tool with a default."""
        try:
            return getattr(self, name)
        except AttributeError:
            return default

    def subst(
        self,
        template: str | list[str],
        *,
        shell: str = "auto",
        **extra: Any,
    ) -> str:
        """Expand variables in a template and return as shell command string.

        Uses both tool namespaces and cross-tool variables. The template
        is expanded to a list of tokens, then converted to a properly
        quoted shell command string.

        Args:
            template: String or list with $var or ${tool.var} references.
            shell: Target shell for quoting ("auto", "bash", "cmd", "powershell", "ninja").
                   Use "ninja" when generating ninja build files.
            **extra: Additional variables for this expansion only.

        Returns:
            Expanded shell command string.
        """
        namespace = self._build_namespace()
        if extra:
            namespace.update(extra)
        tokens = subst(template, namespace)
        return to_shell_command(tokens, shell=shell)

    def subst_list(self, template: str | list[str], **extra: Any) -> list[str]:
        """Expand variables and return as list of tokens.

        Args:
            template: String or list with variable references.
            **extra: Additional variables for this expansion only.

        Returns:
            List of expanded tokens.
        """
        namespace = self._build_namespace()
        if extra:
            namespace.update(extra)
        return subst(template, namespace)

    def _build_namespace(self) -> Namespace:
        """Build a Namespace for variable substitution."""
        tools = self._get_tools()
        vars_dict = self._get_vars()

        # Start with cross-tool variables
        data: dict[str, Any] = dict(vars_dict)

        # Add tool namespaces
        for name, config in tools.items():
            data[name] = config.as_namespace()

        return Namespace(data)

    def clone(self) -> Environment:
        """Create a deep copy of this environment.

        Tool configurations are cloned so modifications don't affect
        the original.

        Returns:
            A new Environment with copied configuration.
        """
        tools = self._get_tools()
        vars_dict = self._get_vars()

        new_env = Environment(defined_at=get_caller_location())

        # Copy cross-tool variables (deep copy lists/dicts)
        new_vars = new_env._get_vars()
        for key, value in vars_dict.items():
            if isinstance(value, list):
                new_vars[key] = list(value)
            elif isinstance(value, dict):
                new_vars[key] = dict(value)
            else:
                new_vars[key] = value

        # Clone tool configurations
        new_tools = new_env._get_tools()
        for name, config in tools.items():
            new_tools[name] = config.clone()

        # Copy toolchain reference (not cloned - it's shared)
        new_env._toolchain = self._toolchain

        # Copy project reference
        new_env._project = object.__getattribute__(self, "_project")

        # Don't copy created_nodes - new environment starts fresh

        return new_env

    @contextmanager
    def override(self, **kwargs: Any) -> Iterator[Environment]:
        """Create a temporary environment with overrides.

        Returns a context manager that yields a cloned environment with
        the specified overrides applied. Useful for building targets with
        temporarily modified settings.

        Args:
            **kwargs: Variables or tool settings to override.
                     For tool settings, use tool__attr notation (e.g., cc__flags)
                     since Python kwargs can't contain dots.

        Yields:
            A cloned Environment with overrides applied. The cloned environment
            is fully independent, so you can also modify it directly within
            the context block.

        Example:
            # Override cross-tool variables
            with env.override(variant="profile") as profile_env:
                project.Program("app_profile", profile_env, sources=["main.cpp"])

            # Override tool settings using double-underscore notation
            with env.override(cxx__flags=["-fno-exceptions"]) as no_except_env:
                project.Library("mylib", no_except_env, sources=["lib.cpp"])

            # The yielded env is a full clone - you can modify it directly too
            with env.override(variant="debug") as debug_env:
                debug_env.cxx.defines.append("EXTRA_DEBUG")
                debug_env.cxx.flags.extend(["-g3", "-fno-omit-frame-pointer"])
                project.Library("mylib_debug", debug_env, sources=["lib.cpp"])
        """
        temp_env = self.clone()

        for key, value in kwargs.items():
            if "__" in key:
                # Tool attribute override: cc__flags -> env.cc.flags
                tool_name, attr_name = key.split("__", 1)
                if temp_env.has_tool(tool_name):
                    tool = getattr(temp_env, tool_name)
                    setattr(tool, attr_name, value)
                else:
                    # Create tool if it doesn't exist
                    tool = temp_env.add_tool(tool_name)
                    setattr(tool, attr_name, value)
            else:
                # Cross-tool variable override
                setattr(temp_env, key, value)

        yield temp_env

    # Convenience methods for common patterns

    def set_variant(self, name: str, **kwargs: Any) -> None:
        """Set the build variant.

        Delegates to the toolchain's apply_variant() method if a toolchain
        is configured. The toolchain is responsible for translating the
        variant name into appropriate tool-specific settings.

        The core knows nothing about what variants mean - it's just a name.
        Each toolchain defines its own semantics (e.g., GCC defines "debug"
        as -O0 -g, while a LaTeX toolchain might use "draft" mode).

        Args:
            name: Variant name (e.g., "debug", "release").
            **kwargs: Toolchain-specific options passed to apply_variant().

        Example:
            env.set_variant("debug")
            env.set_variant("release", extra_flags=["-march=native"])
        """
        if self._toolchain is not None:
            self._toolchain.apply_variant(self, name, **kwargs)
        else:
            # No toolchain - just set the variant name
            self.variant = name

    def Glob(self, pattern: str) -> list[FileNode]:
        """Find files matching a glob pattern.

        This is a placeholder - actual implementation will use
        the project's file tracking.

        Args:
            pattern: Glob pattern (e.g., 'src/*.cpp').

        Returns:
            List of FileNodes matching the pattern.
        """
        # Import here to avoid circular import
        from pathlib import Path as PathlibPath

        from pcons.core.node import FileNode

        # Simple implementation - real one will integrate with Project
        matches = list(PathlibPath(".").glob(pattern))
        return [FileNode(p, defined_at=get_caller_location()) for p in matches]

    def __repr__(self) -> str:
        tools = self._get_tools()
        vars_dict = self._get_vars()
        return (
            f"Environment(tools=[{', '.join(tools.keys())}], "
            f"vars=[{', '.join(vars_dict.keys())}])"
        )
