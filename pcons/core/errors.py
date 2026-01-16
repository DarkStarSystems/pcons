# SPDX-License-Identifier: MIT
"""Custom exceptions for pcons.

All pcons exceptions inherit from PconsError, which includes
optional source location information for better error messages.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pcons.util.source_location import SourceLocation


class PconsError(Exception):
    """Base class for all pcons exceptions.

    Attributes:
        message: The error message.
        location: Optional source location where the error occurred.
    """

    def __init__(
        self,
        message: str,
        location: SourceLocation | None = None,
    ) -> None:
        self.message = message
        self.location = location
        super().__init__(self._format_message())

    def _format_message(self) -> str:
        if self.location:
            return f"{self.location}: {self.message}"
        return self.message


class ConfigureError(PconsError):
    """Error during the configure phase.

    Raised when tool detection fails, feature checks fail,
    or configuration is invalid.
    """


class GenerateError(PconsError):
    """Error during the generate phase.

    Raised when build file generation fails.
    """


class SubstitutionError(PconsError):
    """Error during variable substitution."""


class MissingVariableError(SubstitutionError):
    """Referenced variable does not exist.

    Attributes:
        variable: The name of the missing variable.
    """

    def __init__(
        self,
        variable: str,
        location: SourceLocation | None = None,
    ) -> None:
        self.variable = variable
        super().__init__(f"undefined variable: ${variable}", location)


class CircularReferenceError(SubstitutionError):
    """Circular variable reference detected.

    Attributes:
        chain: The chain of variables forming the cycle.
    """

    def __init__(
        self,
        chain: list[str],
        location: SourceLocation | None = None,
    ) -> None:
        self.chain = chain
        cycle_str = " -> ".join(chain)
        super().__init__(f"circular variable reference: {cycle_str}", location)


class DependencyCycleError(PconsError):
    """Circular dependency detected in the build graph.

    Attributes:
        cycle: The nodes forming the cycle.
    """

    def __init__(
        self,
        cycle: list[str],
        location: SourceLocation | None = None,
    ) -> None:
        self.cycle = cycle
        cycle_str = " -> ".join(cycle)
        super().__init__(f"dependency cycle: {cycle_str}", location)


class MissingSourceError(PconsError):
    """Source file does not exist.

    Attributes:
        path: The path to the missing source file.
    """

    def __init__(
        self,
        path: str,
        location: SourceLocation | None = None,
    ) -> None:
        self.path = path
        super().__init__(f"source file not found: {path}", location)


class ToolNotFoundError(ConfigureError):
    """Required tool was not found.

    Attributes:
        tool: The name of the tool that was not found.
    """

    def __init__(
        self,
        tool: str,
        location: SourceLocation | None = None,
    ) -> None:
        self.tool = tool
        super().__init__(f"tool not found: {tool}", location)


class BuilderError(PconsError):
    """Error in a builder definition or invocation."""
