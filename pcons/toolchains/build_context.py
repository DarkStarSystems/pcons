# SPDX-License-Identifier: MIT
"""ToolchainContext implementations for C/C++ compilation and linking.

These classes hold the tool-specific prefix knowledge (-I/-D/-L/-l for
Unix, /I//D//LIBPATH: for MSVC) that the tool-agnostic core delegates to
via get_env_overrides().
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pcons.core.environment import Environment
    from pcons.core.subst import PathToken
    from pcons.core.target import Target
    from pcons.tools.requirements import EffectiveRequirements


@dataclass
class CompileLinkContext:
    """ToolchainContext for Unix-style C/C++ toolchains (GCC, Clang).

    ``mode`` selects what ``get_env_overrides()`` returns: ``"compile"``
    (includes, defines, flags) or ``"link"`` (libdirs, libs, flags, cmd).
    Paths and names are stored without prefixes; the ``*_prefix`` fields
    supply them at formatting time.
    """

    includes: list[str] = field(default_factory=list)
    defines: list[str] = field(default_factory=list)
    flags: list[str | PathToken] = field(default_factory=list)
    link_flags: list[str | PathToken] = field(default_factory=list)
    libs: list[str] = field(default_factory=list)
    libdirs: list[str] = field(default_factory=list)
    linker_cmd: str | None = None  # Override for link.cmd (e.g., "clang++" for C++)
    mode: str = "compile"  # "compile" or "link"

    # Prefixes - allow toolchains to customize (e.g., MSVC uses /I, /D, /LIBPATH:)
    include_prefix: str = "-I"
    define_prefix: str = "-D"
    libdir_prefix: str = "-L"
    lib_prefix: str = "-l"

    # Runtime-only fields for flag merging (not part of build identity)
    _tool_name: str | None = field(default=None, repr=False, compare=False)
    _env: Environment | None = field(default=None, repr=False, compare=False)

    def get_env_overrides(self) -> dict[str, object]:
        """Return mode-appropriate overrides for env.<tool>.* before subst().

        Keys map directly to tool config attributes (e.g. ``flags`` maps
        to ``{tool_name}.flags``).
        """
        if self.mode == "compile":
            return self._compile_overrides()
        elif self.mode == "link":
            return self._link_overrides()
        return {}

    def _merge_with_base_flags(
        self, tool_name: str | None, flags: list[str | PathToken]
    ) -> list[str | PathToken]:
        """Prepend env.<tool>.flags to `flags`, dropping duplicates.

        Uses the flag-pair-aware merge so separated-argument flags
        (-isystem, -framework, -Xlinker, ...) aren't split by per-token
        deduplication.
        """
        from pcons.core.flags import (
            get_separated_arg_flags_from_toolchains,
            merge_flags,
        )

        base_flags: list[str | PathToken] = []
        if tool_name and self._env and self._env.has_tool(tool_name):
            tool_cfg = getattr(self._env, tool_name, None)
            base_flags = list(getattr(tool_cfg, "flags", None) or [])

        separated_arg_flags = (
            get_separated_arg_flags_from_toolchains(self._env.toolchains)
            if self._env is not None
            else None
        )
        result: list[str | PathToken] = list(base_flags)
        merge_flags(result, flags, separated_arg_flags)
        return result

    def _merge_with_base_libs(self, libs: list[str]) -> list[str]:
        """Append env.link.libs after `libs`, dropping duplicates.

        Env-level libs go last: left-to-right static linkers (GNU ld) only
        pull symbols to satisfy references already seen, so system libs
        like ``pthread``/``dl`` must follow the usage-requirement
        libraries whose undefined symbols they resolve.
        """
        base_libs: list[str] = []
        if self._env and self._env.has_tool("link"):
            link_cfg = getattr(self._env, "link", None)
            base_libs = list(getattr(link_cfg, "libs", None) or [])
        return [lib for lib in libs if lib not in base_libs] + base_libs

    def _compile_overrides(self) -> dict[str, object]:
        """Return compile-time overrides: includes, defines, flags."""
        from pcons.core.subst import ProjectPath

        result: dict[str, object] = {}

        if self.includes:
            result["includes"] = [ProjectPath(p) for p in self.includes]
        if self.defines:
            result["defines"] = list(self.defines)
        if self.flags:
            result["flags"] = self._merge_with_base_flags(self._tool_name, self.flags)

        return result

    def _link_overrides(self) -> dict[str, object]:
        """Return link-time overrides: libdirs, libs, flags, cmd."""
        from pcons.core.subst import ProjectPath

        result: dict[str, object] = {}

        if self.libdirs:
            result["libdirs"] = [ProjectPath(p) for p in self.libdirs]
        merged_libs = self._merge_with_base_libs(self.libs)
        if merged_libs:
            result["libs"] = self._format_libs(merged_libs)
        if self.link_flags:
            result["flags"] = self._merge_with_base_flags("link", self.link_flags)
        if self.linker_cmd:
            result["cmd"] = self.linker_cmd

        return result

    def _format_libs(self, libs: list[str]) -> list[str]:
        """Format library names for the linker. Base passes them unchanged."""
        return list(libs)

    @classmethod
    def from_effective_requirements(
        cls,
        effective: EffectiveRequirements,
        *,
        mode: str = "compile",
        tool_name: str | None = None,
        language: str | None = None,
        env: Environment | None = None,
        target: Target | None = None,
        output_name: str | None = None,
    ) -> CompileLinkContext:
        """Create a CompileLinkContext from EffectiveRequirements.

        Args:
            effective: The computed effective requirements.
            mode: Which overrides to return: "compile" or "link".
            tool_name: The tool name (e.g., "cc", "cxx") for flag merging
                in compile mode.
            language: The link language. "cxx", "objcxx", and "cuda" all
                link via the C++ driver (env.cxx.cmd) for C++ runtime
                linkage — Objective-C++ compiles with the cxx tool, and
                CUDA links through the host C++ toolchain.
            env: The build environment.
            target: The target being built. With *output_name*, lets the
                toolchain inject target-specific link flags (e.g.
                install_name on macOS, SONAME on Linux).
            output_name: The output filename (e.g., ``libfoo.dylib``).

        Returns:
            A CompileLinkContext populated from the requirements.
        """
        # C++ linking needs the C++ driver (g++/clang++) to link the C++
        # runtime. Only override when link.cmd is the C compiler driver:
        # MSVC's link.exe is a separate linker, and using cl.exe as a
        # linker driver breaks flags like /OUT:.
        linker_cmd = None
        if (
            language in ("cxx", "objcxx", "cuda")
            and env is not None
            and env.has_tool("cxx")
        ):
            cxx_cmd = getattr(env.cxx, "cmd", None)
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

        from pcons.core.target import Target

        return cls(
            includes=[str(p) for p in effective.includes],
            defines=list(effective.defines),
            flags=list(effective.compile_flags),
            link_flags=link_flags,
            # link_libs may contain Targets (handled elsewhere) and strings.
            libs=[lib for lib in effective.link_libs if not isinstance(lib, Target)],
            libdirs=[str(p) for p in effective.link_dirs],
            linker_cmd=linker_cmd,
            mode=mode,
            _tool_name=tool_name,
            _env=env,
        )

    def as_hashable_tuple(self) -> tuple:
        """Return a hashable representation for caching."""
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
    """CompileLinkContext with MSVC-specific prefixes (/I, /D, /LIBPATH:)."""

    include_prefix: str = "/I"
    define_prefix: str = "/D"
    libdir_prefix: str = "/LIBPATH:"
    lib_prefix: str = ""  # MSVC uses full library names (foo.lib)

    def _format_libs(self, libs: list[str]) -> list[str]:
        # MSVC uses full library names (kernel32.lib, not -lkernel32).
        # Non-string entries (e.g. PathToken) pass through unchanged.
        formatted: list[str] = []
        for lib in libs:
            if not isinstance(lib, str):
                formatted.append(lib)
            elif lib.endswith(".lib"):
                formatted.append(lib)
            else:
                formatted.append(f"{lib}.lib")
        return formatted
