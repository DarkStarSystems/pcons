# SPDX-License-Identifier: MIT
"""Shared base for WebAssembly toolchains (Emscripten, WASI).

Emscripten and WASI both drive a Unix-like clang/emcc compiler to produce wasm
output, so they share suffix handling, source dispatch, and arch/preset logic.
The pieces that differ (SDK discovery, the program suffix, the linker) stay in
the concrete toolchains.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pcons.core.subst import TargetPath
from pcons.toolchains.unix import UnixToolchain

if TYPE_CHECKING:
    from pcons.core.environment import Environment
    from pcons.tools.toolchain import SourceHandler


class WasmToolchain(UnixToolchain):
    """Base for wasm32 toolchains compiling C/C++ via a clang-like driver.

    Subclasses set `TOOL_NAMES`, `program_suffix` (".js"/".wasm"), and
    `platform_label` (used in the "no shared libraries" error messages), and
    provide SDK discovery via __init__/_configure_tools/setup.

    `TOOL_NAMES` stays on the concrete subclasses: the stub generator treats any
    class carrying it as a real toolchain, so the abstract base must not.
    """

    program_suffix: str
    platform_label: str

    # -- Suffix / naming overrides ------------------------------------------

    def get_output_prefix(self, target_type: str) -> str:
        # wasm targets are Unix-like — always use the "lib" prefix regardless of
        # host platform (e.g. when cross-compiling from Windows).
        if target_type in ("static_library", "shared_library"):
            return "lib"
        return ""

    def get_output_suffix(self, target_type: str) -> str:
        if target_type == "program":
            return self.program_suffix
        if target_type == "shared_library":
            raise NotImplementedError(
                f"{self.platform_label} does not support shared libraries. "
                "Use StaticLibrary instead, or target a native platform."
            )
        return ".a"  # static library

    def get_compile_flags_for_target_type(self, target_type: str) -> list[str]:
        # No -fPIC needed for WebAssembly
        if target_type == "shared_library":
            raise NotImplementedError(
                f"{self.platform_label} does not support shared libraries."
            )
        return []

    # -- Source handler ------------------------------------------------------

    def get_source_handler(self, suffix: str) -> SourceHandler | None:
        """Handle C/C++ sources (no Objective-C or assembly for wasm)."""
        from pcons.tools.toolchain import SourceHandler

        depfile = TargetPath(suffix=".d")
        # Check case-sensitive .C (C++ on Unix) before lowering
        if suffix == ".C":
            return SourceHandler("cxx", "cxx", ".o", depfile, "gcc")
        suffix_lower = suffix.lower()
        if suffix_lower == ".c":
            return SourceHandler("cc", "c", ".o", depfile, "gcc")
        if suffix_lower in (".cpp", ".cxx", ".cc", ".c++"):
            return SourceHandler("cxx", "cxx", ".o", depfile, "gcc")
        return None

    # -- Variant / arch overrides -------------------------------------------

    def apply_target_arch(self, env: Environment, arch: str, **kwargs: Any) -> None:
        # wasm32 is the only architecture; ignore arch requests
        super(UnixToolchain, self).apply_target_arch(env, "wasm32", **kwargs)

    def apply_cross_preset(self, env: Environment, preset: Any) -> None:
        # Sysroot (if any) is handled by setup(); just apply extra flags.
        if hasattr(preset, "extra_compile_flags") and preset.extra_compile_flags:
            for tool_name in ("cc", "cxx"):
                if env.has_tool(tool_name):
                    tool = getattr(env, tool_name)
                    if hasattr(tool, "flags") and isinstance(tool.flags, list):
                        tool.flags.extend(preset.extra_compile_flags)
        if hasattr(preset, "extra_link_flags") and preset.extra_link_flags:
            if env.has_tool("link"):
                if isinstance(env.link.flags, list):
                    env.link.flags.extend(preset.extra_link_flags)
