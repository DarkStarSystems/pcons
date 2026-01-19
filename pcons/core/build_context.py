# SPDX-License-Identifier: MIT
"""Build context classes for toolchain-specific build information.

This module provides context classes that implement the ToolchainContext protocol.
These classes encapsulate toolchain-specific build information and provide
formatted variables for use in command templates.

The context approach decouples the core from domain-specific concepts:
- Core only knows about ToolchainContext.get_variables() -> dict[str, str]
- Toolchains define what variables exist and how they're formatted
- Generators write variables without knowing their semantics
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pcons.core.requirements import EffectiveRequirements


@dataclass
class CompileLinkContext:
    """Context for C/C++ compilation and linking.

    This class implements the ToolchainContext protocol for C/C++ toolchains.
    It holds all the information needed for compilation and linking, and
    formats it into variables suitable for command templates.

    The formatting (prefixes like -I, -D, -L, -l) is done here rather than
    in the generator, allowing different toolchains to use different prefixes.

    Attributes:
        includes: Include directories (without -I prefix).
        defines: Preprocessor definitions (without -D prefix).
        flags: Additional compiler flags.
        link_flags: Linker flags.
        libs: Libraries to link (without -l prefix).
        libdirs: Library search directories (without -L prefix).
        include_prefix: Prefix for include directories (default: "-I").
        define_prefix: Prefix for preprocessor definitions (default: "-D").
        libdir_prefix: Prefix for library directories (default: "-L").
        lib_prefix: Prefix for libraries (default: "-l").
    """

    includes: list[str] = field(default_factory=list)
    defines: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    link_flags: list[str] = field(default_factory=list)
    libs: list[str] = field(default_factory=list)
    libdirs: list[str] = field(default_factory=list)

    # Prefixes - allow toolchains to customize (e.g., MSVC uses /I, /D, /LIBPATH:)
    include_prefix: str = "-I"
    define_prefix: str = "-D"
    libdir_prefix: str = "-L"
    lib_prefix: str = "-l"

    def get_variables(self) -> dict[str, list[str]]:
        """Return variables for build statement.

        Keys match placeholders in command templates:
        - includes: Include flags (e.g., ["-I/path1", "-I/path2"])
        - defines: Define flags (e.g., ["-DFOO", "-DBAR=1"])
        - extra_flags: Additional compiler flags
        - ldflags: Linker flags
        - libs: Library flags (e.g., ["-lfoo", "-lbar"])
        - libdirs: Library directory flags (e.g., ["-L/path1", "-L/path2"])

        Values are lists of individual tokens. The generator is responsible
        for joining them with appropriate quoting for the target format.
        This ensures paths with spaces and defines with special characters
        are handled correctly.

        Returns:
            Dictionary mapping variable names to lists of string tokens.
        """
        result: dict[str, list[str]] = {}

        if self.includes:
            result["includes"] = [
                f"{self.include_prefix}{inc}" for inc in self.includes
            ]

        if self.defines:
            result["defines"] = [f"{self.define_prefix}{d}" for d in self.defines]

        if self.flags:
            result["extra_flags"] = list(self.flags)

        if self.link_flags:
            result["ldflags"] = list(self.link_flags)

        if self.libs:
            result["libs"] = [f"{self.lib_prefix}{lib}" for lib in self.libs]

        if self.libdirs:
            result["libdirs"] = [f"{self.libdir_prefix}{d}" for d in self.libdirs]

        return result

    @classmethod
    def from_effective_requirements(
        cls, effective: EffectiveRequirements
    ) -> CompileLinkContext:
        """Create a CompileLinkContext from EffectiveRequirements.

        This factory method bridges the current EffectiveRequirements system
        to the new context-based approach.

        Args:
            effective: The computed effective requirements.

        Returns:
            A CompileLinkContext populated from the requirements.
        """
        return cls(
            includes=[str(p) for p in effective.includes],
            defines=list(effective.defines),
            flags=list(effective.compile_flags),
            link_flags=list(effective.link_flags),
            libs=list(effective.link_libs),
            libdirs=[str(p) for p in effective.link_dirs],
        )

    def as_hashable_tuple(self) -> tuple:
        """Return hashable representation for caching.

        This can be used as a dictionary key or set member to identify
        unique build configurations.

        Returns:
            A tuple containing all context data in a hashable form.
        """
        return (
            tuple(self.includes),
            tuple(self.defines),
            tuple(self.flags),
            tuple(self.link_flags),
            tuple(self.libs),
            tuple(self.libdirs),
        )


@dataclass
class MsvcCompileLinkContext(CompileLinkContext):
    """Context for MSVC compilation and linking.

    Uses MSVC-specific prefixes for flags.
    """

    include_prefix: str = "/I"
    define_prefix: str = "/D"
    libdir_prefix: str = "/LIBPATH:"
    lib_prefix: str = ""  # MSVC uses full library names (foo.lib)

    def get_variables(self) -> dict[str, list[str]]:
        """Return variables for MSVC build statement.

        MSVC has some differences from Unix toolchains:
        - Libraries are specified by full name (foo.lib), not -lfoo
        - Library paths use /LIBPATH: prefix

        Values are lists of individual tokens. The generator is responsible
        for joining them with appropriate quoting for the target format.

        Returns:
            Dictionary mapping variable names to lists of string tokens.
        """
        result: dict[str, list[str]] = {}

        if self.includes:
            result["includes"] = [
                f"{self.include_prefix}{inc}" for inc in self.includes
            ]

        if self.defines:
            result["defines"] = [f"{self.define_prefix}{d}" for d in self.defines]

        if self.flags:
            result["extra_flags"] = list(self.flags)

        if self.link_flags:
            result["ldflags"] = list(self.link_flags)

        if self.libs:
            # MSVC uses full library names (kernel32.lib, not -lkernel32)
            # If library doesn't have .lib suffix, add it
            formatted_libs = []
            for lib in self.libs:
                if lib.endswith(".lib"):
                    formatted_libs.append(lib)
                else:
                    formatted_libs.append(f"{lib}.lib")
            result["libs"] = formatted_libs

        if self.libdirs:
            result["libdirs"] = [f"{self.libdir_prefix}{d}" for d in self.libdirs]

        return result

    @classmethod
    def from_effective_requirements(
        cls, effective: EffectiveRequirements
    ) -> MsvcCompileLinkContext:
        """Create a MsvcCompileLinkContext from EffectiveRequirements.

        This factory method bridges the current EffectiveRequirements system
        to the new context-based approach, with MSVC-style prefixes.

        Args:
            effective: The computed effective requirements.

        Returns:
            A MsvcCompileLinkContext populated from the requirements.
        """
        return cls(
            includes=[str(p) for p in effective.includes],
            defines=list(effective.defines),
            flags=list(effective.compile_flags),
            link_flags=list(effective.link_flags),
            libs=list(effective.link_libs),
            libdirs=[str(p) for p in effective.link_dirs],
        )
