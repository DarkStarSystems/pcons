# SPDX-License-Identifier: MIT
"""Toolchain protocol and base implementation.

A Toolchain is a coordinated set of Tools that work together
(e.g., GCC toolchain includes gcc, g++, ar, ld with compatible flags).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from pcons.core.environment import Environment
    from pcons.tools.tool import Tool


@runtime_checkable
class Toolchain(Protocol):
    """Protocol for toolchains.

    A Toolchain represents a coordinated set of tools that work together.
    Switching toolchains switches all related tools atomically.
    """

    @property
    def name(self) -> str:
        """Toolchain name (e.g., 'gcc', 'llvm', 'msvc')."""
        ...

    @property
    def tools(self) -> dict[str, Tool]:
        """Tools in this toolchain, keyed by tool name."""
        ...

    @property
    def language_priority(self) -> dict[str, int]:
        """Language priority for linker selection.

        Higher values = stronger language. When linking objects from
        multiple languages, use the linker for the highest-priority
        language.
        """
        ...

    def configure(self, config: object) -> bool:
        """Configure all tools in this toolchain.

        Args:
            config: Configure context.

        Returns:
            True if the toolchain is available and configured.
        """
        ...

    def setup(self, env: Environment) -> None:
        """Add all tools to an environment.

        Args:
            env: Environment to set up.
        """
        ...


class BaseToolchain(ABC):
    """Abstract base class for toolchains.

    Provides common functionality for toolchains. Subclasses must
    provide the list of tools and configure logic.
    """

    # Default language priorities (higher = stronger)
    DEFAULT_LANGUAGE_PRIORITY: dict[str, int] = {
        "c": 1,
        "cxx": 2,
        "objc": 2,
        "objcxx": 3,
        "fortran": 3,
        "cuda": 4,
    }

    def __init__(self, name: str) -> None:
        """Initialize a toolchain.

        Args:
            name: Toolchain name.
        """
        self._name = name
        self._tools: dict[str, Tool] = {}
        self._configured = False

    @property
    def name(self) -> str:
        return self._name

    @property
    def tools(self) -> dict[str, Tool]:
        return self._tools

    @property
    def language_priority(self) -> dict[str, int]:
        """Override in subclasses if needed."""
        return self.DEFAULT_LANGUAGE_PRIORITY

    def configure(self, config: object) -> bool:
        """Configure all tools.

        Subclasses should override _configure_tools() to set up
        the _tools dict.
        """
        if self._configured:
            return True

        result = self._configure_tools(config)
        self._configured = result
        return result

    @abstractmethod
    def _configure_tools(self, config: object) -> bool:
        """Configure the toolchain's tools.

        Subclasses implement this to detect and configure tools.

        Args:
            config: Configure context.

        Returns:
            True if configuration succeeded.
        """
        ...

    def setup(self, env: Environment) -> None:
        """Set up all tools in the environment."""
        for tool in self._tools.values():
            tool.setup(env)

    def get_linker_for_languages(self, languages: set[str]) -> str:
        """Determine which tool should link based on languages used.

        Args:
            languages: Set of language names (e.g., {'c', 'cxx'}).

        Returns:
            Tool name to use for linking.
        """
        if not languages:
            return "link"

        # Find the highest priority language
        priority = self.language_priority
        max_priority = -1
        max_lang = "c"

        for lang in languages:
            p = priority.get(lang, 0)
            if p > max_priority:
                max_priority = p
                max_lang = lang

        # Map language to linker tool
        # (subclasses may override this mapping)
        return self._linker_for_language(max_lang)

    def _linker_for_language(self, language: str) -> str:
        """Get the linker tool name for a language.

        Override in subclasses if the mapping is different.
        """
        # Default: use the language's compiler as linker
        # (e.g., 'cxx' means use g++ to link)
        if language == "c":
            return "cc"
        elif language in ("cxx", "objcxx"):
            return "cxx"
        elif language == "fortran":
            return "fortran"
        elif language == "cuda":
            return "cuda"
        else:
            return "link"

    def __repr__(self) -> str:
        tools = ", ".join(self._tools.keys())
        return f"{self.__class__.__name__}({self.name!r}, tools=[{tools}])"
