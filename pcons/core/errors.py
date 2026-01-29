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
        available_keys: Keys that were available in the namespace.
        template: The template being expanded (if known).
    """

    def __init__(
        self,
        variable: str,
        location: SourceLocation | None = None,
        available_keys: list[str] | None = None,
        template: str | None = None,
    ) -> None:
        self.variable = variable
        self.available_keys = available_keys
        self.template = template

        msg = f"undefined variable: ${variable}"

        # Add suggestions for similar variable names
        if available_keys:
            var_prefix = variable.split(".")[0] if "." in variable else variable
            similar = [k for k in available_keys if var_prefix in k][:3]
            if similar:
                msg += (
                    f"\n  Available in '{var_prefix}' namespace: {', '.join(similar)}"
                )
            elif len(available_keys) <= 10:
                msg += f"\n  Available variables: {', '.join(sorted(available_keys))}"

        if template:
            msg += (
                f"\n  In template: {template[:80]}{'...' if len(template) > 80 else ''}"
            )

        super().__init__(msg, location)


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
        target_name: The target that references this source (if known).
    """

    def __init__(
        self,
        path: str,
        location: SourceLocation | None = None,
        target_name: str | None = None,
    ) -> None:
        self.path = path
        self.target_name = target_name

        msg = f"source file not found: {path}"
        if target_name:
            msg += f"\n  Referenced by target: {target_name}"

        # Suggest checking the path
        from pathlib import Path as P

        p = P(path)
        if not p.is_absolute():
            msg += "\n  Tip: Path is relative. Check that it's relative to the source directory."

        super().__init__(msg, location)


class ToolNotFoundError(ConfigureError):
    """Required tool was not found.

    Attributes:
        tool: The name of the tool that was not found.
        hint: Optional hint for how to install the tool.
    """

    # Common installation hints for known tools
    _INSTALL_HINTS = {
        "ninja": "Install ninja: https://ninja-build.org/ or 'brew install ninja'",
        "clang": "Install LLVM: https://llvm.org/ or 'xcode-select --install' on macOS",
        "gcc": "Install GCC: 'brew install gcc' on macOS, 'apt install gcc' on Ubuntu",
        "nvcc": "Install CUDA Toolkit: https://developer.nvidia.com/cuda-downloads",
    }

    def __init__(
        self,
        tool: str,
        location: SourceLocation | None = None,
        hint: str | None = None,
    ) -> None:
        self.tool = tool
        self.hint = hint or self._INSTALL_HINTS.get(tool)

        msg = f"tool not found: {tool}"
        if self.hint:
            msg += f"\n  {self.hint}"

        super().__init__(msg, location)


class PackageNotFoundError(ConfigureError):
    """Required package was not found by any finder.

    Attributes:
        package_name: Name of the package.
        version: Version requirement that was requested.
    """

    def __init__(
        self,
        package_name: str,
        version: str | None = None,
        location: SourceLocation | None = None,
    ) -> None:
        self.package_name = package_name
        self.version_req = version

        msg = f"package not found: {package_name}"
        if version:
            msg += f" (version {version})"
        msg += "\n  Tip: Ensure the package is installed and discoverable by pkg-config or system paths."

        super().__init__(msg, location)


class BuilderError(PconsError):
    """Error in a builder definition or invocation."""
