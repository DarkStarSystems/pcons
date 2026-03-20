# SPDX-License-Identifier: MIT
"""Build context classes for C/C++ toolchains.

This module provides context classes that implement the ToolchainContext protocol
for C/C++ compilation and linking. These classes contain tool-specific knowledge
(prefixes like -I, -D, -L, -l for Unix or /I, /D, /LIBPATH: for MSVC).

The context approach decouples the core from domain-specific concepts:
- Core only knows about ToolchainContext.get_env_overrides() -> dict[str, object]
- Toolchains define what variables exist and how they're formatted
- Generators use these overrides to expand command templates
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pcons.core.environment import Environment
    from pcons.core.requirements import EffectiveRequirements
    from pcons.core.target import Target


@dataclass
class CompileLinkContext:
    """Context for Unix-style C/C++ compilation and linking.

    This class implements the ToolchainContext protocol for Unix-style C/C++
    toolchains (GCC, Clang). It holds all the information needed for compilation
    and linking, and formats it into variables suitable for command templates.

    The formatting (prefixes like -I, -D, -L, -l) is done here rather than
    in the generator, allowing different toolchains to use different prefixes.

    Attributes:
        includes: Include directories (without -I prefix).
        defines: Preprocessor definitions (without -D prefix).
        flags: Additional compiler flags.
        link_flags: Linker flags.
        libs: Libraries to link (without -l prefix).
        libdirs: Library search directories (without -L prefix).
        linker_cmd: Override for link.cmd (e.g., "clang++" for C++ linking).
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
    linker_cmd: str | None = None  # Override for link.cmd (e.g., "clang++" for C++)

    # Prefixes - allow toolchains to customize (e.g., MSVC uses /I, /D, /LIBPATH:)
    include_prefix: str = "-I"
    define_prefix: str = "-D"
    libdir_prefix: str = "-L"
    lib_prefix: str = "-l"

    def get_env_overrides(self) -> dict[str, object]:
        """Return values to set on env.<tool>.* before subst().

        These values are set on the environment's tool namespace so that
        template expressions like ${prefix(cc.iprefix, cc.includes)} are
        expanded during subst() with the effective requirements.

        Path values (includes, libdirs) are wrapped in ProjectPath markers
        so that the prefix() function creates PathToken objects. This allows
        generators to apply appropriate path relativization.

        Returns:
            Dictionary mapping variable names to values.
            Lists are returned as-is for the prefix() function to process.
        """
        from pcons.core.subst import ProjectPath

        result: dict[str, object] = {}

        # Compile-time settings
        # Wrap include paths in ProjectPath for generator-specific relativization
        if self.includes:
            result["includes"] = [ProjectPath(p) for p in self.includes]
        if self.defines:
            result["defines"] = list(self.defines)
        if self.flags:
            result["extra_flags"] = list(self.flags)

        # Link-time settings
        # Wrap library paths in ProjectPath for generator-specific relativization
        if self.libdirs:
            result["libdirs"] = [ProjectPath(p) for p in self.libdirs]
        if self.libs:
            result["libs"] = list(self.libs)
        if self.link_flags:
            result["ldflags"] = list(self.link_flags)

        # Linker command override (e.g., "clang++" for C++ linking)
        if self.linker_cmd:
            result["linker_cmd"] = self.linker_cmd

        return result

    @classmethod
    def from_effective_requirements(
        cls,
        effective: EffectiveRequirements,
        language: str | None = None,
        env: Environment | None = None,
        target: Target | None = None,
        output_name: str | None = None,
    ) -> CompileLinkContext:
        """Create a CompileLinkContext from EffectiveRequirements.

        This factory method bridges the current EffectiveRequirements system
        to the new context-based approach.

        Args:
            effective: The computed effective requirements.
            language: The link language (e.g., "c", "cxx"). If "cxx" and env
                has a "cxx" tool, the linker_cmd will be set to env.cxx.cmd
                to ensure proper C++ runtime linkage.
            env: The build environment, used to look up the C++ compiler
                command when linking C++ code.
            target: The target being built. When provided along with
                *output_name*, the toolchain may inject target-specific
                link flags (e.g. install_name on macOS, SONAME on Linux).
            output_name: The output filename (e.g., ``libfoo.dylib``).

        Returns:
            A CompileLinkContext populated from the requirements.
        """
        # Determine linker command override for C++ linking.
        # For GCC/Clang, g++/clang++ must be used as the linker driver to link
        # the C++ runtime — the link tool uses the C compiler (gcc/clang) by
        # default, so we override it with the C++ driver.
        # For MSVC, link.exe is the linker (separate from cl.exe), and using
        # cl.exe as a linker driver breaks flags like /OUT:. We only override
        # when link.cmd == cc.cmd (i.e., the linker is the C compiler driver).
        linker_cmd = None
        if language == "cxx" and env is not None and env.has_tool("cxx"):
            cxx_cmd = getattr(env.cxx, "cmd", None)
            # Only override when the link tool is using the C compiler as its cmd
            # (GCC/Clang pattern). Skip when the link tool has its own separate
            # executable (MSVC link.exe pattern).
            cc_cmd = getattr(env.cc, "cmd", None) if env.has_tool("cc") else None
            link_cmd = getattr(env.link, "cmd", None) if env.has_tool("link") else None
            if cxx_cmd and link_cmd is not None and link_cmd == cc_cmd:
                linker_cmd = cxx_cmd
            elif cxx_cmd and link_cmd is None:
                linker_cmd = cxx_cmd
        elif language == "fortran" and env is not None and env.has_tool("fc"):
            fc_cmd = getattr(env.fc, "cmd", None)
            if fc_cmd:
                linker_cmd = fc_cmd

        link_flags = list(effective.link_flags)

        # Let the toolchain inject target-specific link flags
        if target is not None and output_name is not None and env is not None:
            toolchain = env._toolchain
            if toolchain is not None:
                extra = toolchain.get_link_flags_for_target(
                    target, output_name, link_flags
                )
                for flag in extra:
                    if flag not in link_flags:
                        link_flags.append(flag)

        return cls(
            includes=[str(p) for p in effective.includes],
            defines=list(effective.defines),
            flags=list(effective.compile_flags),
            link_flags=link_flags,
            libs=list(effective.link_libs),
            libdirs=[str(p) for p in effective.link_dirs],
            linker_cmd=linker_cmd,
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
            self.linker_cmd,
        )


@dataclass
class MsvcCompileLinkContext(CompileLinkContext):
    """Context for MSVC compilation and linking.

    Uses MSVC-specific prefixes for flags (/I, /D, /LIBPATH:).
    """

    include_prefix: str = "/I"
    define_prefix: str = "/D"
    libdir_prefix: str = "/LIBPATH:"
    lib_prefix: str = ""  # MSVC uses full library names (foo.lib)

    def get_env_overrides(self) -> dict[str, object]:
        """Return values to set on env.<tool>.* before subst().

        MSVC-specific: libraries use full names (foo.lib) rather than -lfoo.

        Returns:
            Dictionary mapping variable names to values.
        """
        from pcons.core.subst import ProjectPath

        result: dict[str, object] = {}

        # Compile-time settings
        if self.includes:
            result["includes"] = [ProjectPath(p) for p in self.includes]
        if self.defines:
            result["defines"] = list(self.defines)
        if self.flags:
            result["extra_flags"] = list(self.flags)

        # Link-time settings
        if self.libdirs:
            result["libdirs"] = [ProjectPath(p) for p in self.libdirs]
        if self.libs:
            # MSVC uses full library names (kernel32.lib, not -lkernel32)
            formatted_libs = []
            for lib in self.libs:
                if lib.endswith(".lib"):
                    formatted_libs.append(lib)
                else:
                    formatted_libs.append(f"{lib}.lib")
            result["libs"] = formatted_libs
        if self.link_flags:
            result["ldflags"] = list(self.link_flags)

        # Linker command override (e.g., "cl" for C++ linking with MSVC)
        if self.linker_cmd:
            result["linker_cmd"] = self.linker_cmd

        return result

    @classmethod
    def from_effective_requirements(
        cls,
        effective: EffectiveRequirements,
        language: str | None = None,
        env: Environment | None = None,
        target: Target | None = None,
        output_name: str | None = None,
    ) -> MsvcCompileLinkContext:
        """Create a MsvcCompileLinkContext from EffectiveRequirements.

        This factory method bridges the current EffectiveRequirements system
        to the new context-based approach, with MSVC-style prefixes.

        Args:
            effective: The computed effective requirements.
            language: Unused for MSVC (kept for interface compatibility).
            env: Unused for MSVC (kept for interface compatibility).
            target: Unused for MSVC (kept for interface compatibility).
            output_name: Unused for MSVC (kept for interface compatibility).

        Returns:
            A MsvcCompileLinkContext populated from the requirements.
        """
        # MSVC always uses link.exe regardless of language (no linker_cmd override).
        # Unlike GCC/Clang where g++/clang++ must be used as the linker driver for C++
        # runtime linkage, MSVC link.exe handles all languages automatically.
        return cls(
            includes=[str(p) for p in effective.includes],
            defines=list(effective.defines),
            flags=list(effective.compile_flags),
            link_flags=list(effective.link_flags),
            libs=list(effective.link_libs),
            libdirs=[str(p) for p in effective.link_dirs],
            linker_cmd=None,
        )
