# SPDX-License-Identifier: MIT
"""Tool protocol and base implementation.

A Tool knows how to perform a specific type of transformation (e.g.,
compiling C files). Tools attach to Environments and provide Builders.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from pcons.core.builder import Builder
    from pcons.core.environment import Environment
    from pcons.core.toolconfig import ToolConfig


@runtime_checkable
class Tool(Protocol):
    """Protocol for tools.

    A Tool represents a specific build tool (e.g., C compiler, linker,
    protobuf compiler). Tools provide:
    - Configuration detection
    - Default variable values
    - Builders that create targets
    """

    @property
    def name(self) -> str:
        """Tool name (e.g., 'cc', 'cxx', 'ar', 'link')."""
        ...

    def configure(self, config: object) -> ToolConfig | None:
        """Detect and configure this tool.

        Called during the configure phase. Should detect the tool's
        executable, version, and capabilities.

        Args:
            config: Configure context for running checks.

        Returns:
            ToolConfig with detected settings, or None if not available.
        """
        ...

    def setup(self, env: Environment) -> None:
        """Initialize the tool in an environment.

        Called when adding the tool to an environment. Should create
        the tool's namespace and set default variables.

        Args:
            env: Environment to set up.
        """
        ...

    def builders(self) -> dict[str, Builder]:
        """Return builders this tool provides.

        Returns:
            Dict mapping builder names to Builder instances.
        """
        ...


class BaseTool(ABC):
    """Abstract base class for tools.

    Provides common functionality for tools. Subclasses must implement
    the abstract methods.
    """

    def __init__(self, name: str = "", *, language: str | None = None) -> None:
        """Initialize a tool.

        Args:
            name: Tool name. Subclasses should always provide this.
            language: Language this tool handles (for linker selection).
        """
        self._name = name
        self._language = language
        self._builders: dict[str, Builder] | None = None

    @property
    def name(self) -> str:
        return self._name

    @property
    def language(self) -> str | None:
        return self._language

    def configure(self, config: object) -> ToolConfig | None:
        """Default implementation returns None (tool not configured)."""
        return None

    def setup(self, env: Environment) -> None:
        """Set up the tool namespace with default values."""
        # Create or get the tool's namespace
        tool_config = env.add_tool(self._name)

        # Set default values
        defaults = self.default_vars()
        for key, value in defaults.items():
            if key not in tool_config:
                tool_config.set(key, value)

        # Attach builders to the tool config
        for builder_name, builder in self.builders().items():
            # Make builder callable from tool config (e.g., env.cc.Object())
            setattr(tool_config, builder_name, self._make_builder_method(env, builder))

    def _make_builder_method(self, env: Environment, builder: Builder) -> BuilderMethod:
        """Create a bound method for calling a builder from the tool config."""
        return BuilderMethod(env, builder)

    @abstractmethod
    def default_vars(self) -> dict[str, object]:
        """Return default variable values for this tool.

        Subclasses should return a dict with default values like:
        {
            'cmd': 'gcc',
            'flags': [],
            'includes': [],
            'defines': [],
        }
        """
        ...

    @abstractmethod
    def builders(self) -> dict[str, Builder]:
        """Return builders this tool provides."""
        ...

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.name!r})"


class StandaloneTool(BaseTool):
    """Tool that's always available without external program detection.

    Standalone tools don't require toolchains or external program detection.
    They are auto-registered with environments and provide builders for
    common operations like file installation and archiving.

    Unlike toolchain-provided tools (cc, cxx, etc.) which require detecting
    compilers, standalone tools use Python's built-in capabilities or
    cross-platform helper scripts.

    Example:
        class InstallTool(StandaloneTool):
            def __init__(self) -> None:
                super().__init__("install")

            def default_vars(self) -> dict[str, object]:
                return {"copycmd": "python -m pcons.util.commands copy $SOURCE $TARGET"}

            def builders(self) -> dict[str, Builder]:
                return {}  # Builders registered via @builder decorator
    """

    def configure(self, config: object) -> ToolConfig | None:
        """Standalone tools are always available.

        Returns a valid ToolConfig since no external detection is needed.
        """
        from pcons.core.toolconfig import ToolConfig

        return ToolConfig(self.name)


class BuilderMethod:
    """A callable that invokes a builder with an environment.

    This allows calling builders as methods on tool configs:
        env.cc.Object('foo.o', 'foo.c')
        env.cc.Object(build_dir / 'foo.o', src_dir / 'foo.c')  # Paths work too
    """

    def __init__(self, env: Environment, builder: Builder) -> None:
        self._env = env
        self._builder = builder

    def __call__(
        self,
        target: str | Path | None = None,
        sources: list[str | Path] | str | Path | None = None,
        **kwargs: object,
    ) -> list:
        """Invoke the builder.

        Args:
            target: Target file path (str or Path).
            sources: Source file(s) (str, Path, or list of either).
            **kwargs: Additional builder options.

        Returns:
            List of created nodes.
        """
        from pathlib import Path as PathlibPath

        from pcons.core.node import Node

        # Normalize sources to a list
        source_list: list[str | PathlibPath | Node]
        if sources is None:
            source_list = []
        elif isinstance(sources, (str, PathlibPath)):
            source_list = [sources]
        else:
            source_list = list(sources)

        nodes = self._builder(self._env, target, source_list, **kwargs)

        # Register created nodes with the environment
        for node in nodes:
            self._env.register_node(node)

        return nodes

    def __repr__(self) -> str:
        return f"BuilderMethod({self._builder.name})"
