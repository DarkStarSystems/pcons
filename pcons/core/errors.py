# SPDX-License-Identifier: MIT
"""Custom exceptions for pcons.

All pcons exceptions inherit from PconsError, which includes
optional source location information for better error messages.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pcons.core.target import Target
    from pcons.util.source_location import SourceLocation


class PconsError(Exception):
    """Base class for all pcons exceptions.

    Attributes:
        message: The error message.
        location: Optional source location where the error occurred.
        fatal: Whether this error may be reported and carried past. Only
            errors collected rather than raised — see `Project.validate()` —
            are ever reported instead of stopping the run, and an error that
            leaves the build description unusable sets this to stop it
            anyway. The decision belongs with the error, which knows what it
            found; not with whoever collected it.
    """

    fatal: bool = False

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

        # Hint about $$ escaping for shell/linker variables
        if "." not in variable:
            msg += (
                f"\n  If ${variable} is a shell or linker variable"
                f" (e.g., $ORIGIN), use $${variable} to pass it through literally."
            )

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

    Always fatal: a cycle has no build order, so nothing downstream of it
    could be built even if the build files were written.

    Attributes:
        cycle: The nodes forming the cycle.
    """

    fatal = True

    def __init__(
        self,
        cycle: list[str],
        location: SourceLocation | None = None,
    ) -> None:
        self.cycle = cycle
        cycle_str = " -> ".join(cycle)
        super().__init__(f"dependency cycle: {cycle_str}", location)


class DuplicateTargetError(PconsError):
    """Two targets answer to one qualified name.

    Always fatal: a name and an environment are a target's identity, so the
    build graph cannot tell the two apart, and neither can anything that names
    a target on the command line.

    Attributes:
        qualified_name: The name both targets answer to.
    """

    fatal = True

    def __init__(
        self,
        qualified_name: str,
        first: Target,
        second: Target,
        location: SourceLocation | None = None,
    ) -> None:
        self.qualified_name = qualified_name
        super().__init__(
            f"two targets are both named '{qualified_name}'\n"
            f"  first  at {first.defined_at}\n"
            f"  second at {second.defined_at}\n"
            f"A name and an environment are a target's identity, so they have "
            f"to differ. Name the environments apart, or give one target "
            f"another name.",
            location,
        )


class MissingSourceError(PconsError):
    """Source file does not exist.

    Attributes:
        path: The path to the missing source file.
        target_name: The target that references this source (if known).
        produced: ``(target name, real path, path below the build dir)`` when
            a target builds a file of this name inside the build directory —
            the source almost certainly meant that target's output.
    """

    def __init__(
        self,
        path: str,
        location: SourceLocation | None = None,
        target_name: str | None = None,
        produced: tuple[str, str, str] | None = None,
    ) -> None:
        self.path = path
        self.target_name = target_name
        self.produced = produced
        # A source naming another target's output cannot be carried past:
        # the build file would name a path no rule produces, and the build
        # tool cannot even load that. A source that is merely absent still
        # reports and continues, so a script can be fixed in one pass.
        self.fatal = produced is not None

        msg = f"source file not found: {path}"
        if target_name:
            msg += f"\n  Referenced by target: {target_name}"

        if produced:
            # Sources are project-root-relative; build outputs live under the
            # build directory. Naming a generated file by its build-dir-relative
            # path therefore points into the source tree, where nothing is.
            builder_name, real_path, below_build_dir = produced
            # The suggestion uses project.build_dir rather than the
            # directory's name: that name is the -B / PCONS_BUILD_DIR choice,
            # so a literal would work only for whoever ran the build today.
            msg += (
                f"\n  Target '{builder_name}' builds a file of that path, as "
                f"'{real_path}'.\n"
                f"  To fix, either pass that target itself, or use the real "
                f"path:\n"
                f'      sources=[project.build_dir / "{below_build_dir}"]'
            )
        elif not Path(path).is_absolute():
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
