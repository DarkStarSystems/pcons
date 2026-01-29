# SPDX-License-Identifier: MIT
"""Shared base class for MSVC-compatible toolchains.

This module provides common functionality for toolchains that produce
MSVC-compatible binaries on Windows (MSVC and clang-cl).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pcons.tools.toolchain import BaseToolchain

if TYPE_CHECKING:
    from pcons.core.environment import Environment
    from pcons.tools.toolchain import AuxiliaryInputHandler, SourceHandler

logger = logging.getLogger(__name__)


class MsvcCompatibleToolchain(BaseToolchain):
    """Base class for MSVC-compatible toolchains (MSVC and clang-cl).

    Provides shared implementations for methods that are identical between
    MSVC and clang-cl toolchains, including:
    - Source file handling (.c, .cpp, .rc, .asm)
    - Auxiliary input handling (.def, .manifest)
    - Output naming conventions (.obj, .lib, .dll, .exe)
    - Architecture flag handling (/MACHINE:xxx)
    - Build variant handling (debug, release, etc.)

    Subclasses should override methods where behavior differs, such as
    cross-compilation flags or tool configuration.
    """

    # Named flag presets for common development workflows (MSVC-compatible).
    MSVC_PRESETS: dict[str, dict[str, list[str]]] = {
        "warnings": {
            "compile_flags": ["/W4", "/WX"],
        },
        "sanitize": {
            "compile_flags": ["/fsanitize=address"],
        },
        "profile": {
            "link_flags": ["/PROFILE"],
        },
        "lto": {
            "compile_flags": ["/GL"],
            "link_flags": ["/LTCG"],
        },
        "hardened": {
            "compile_flags": ["/GS", "/guard:cf"],
            "link_flags": ["/DYNAMICBASE", "/NXCOMPAT", "/guard:cf"],
        },
    }

    # Architecture to MSVC machine type mapping (shared by MSVC and clang-cl)
    MSVC_MACHINE_MAP: dict[str, str] = {
        "x64": "X64",
        "x86": "X86",
        "arm64": "ARM64",
        "arm64ec": "ARM64EC",
        # Common aliases
        "amd64": "X64",
        "x86_64": "X64",
        "i386": "X86",
        "i686": "X86",
        "aarch64": "ARM64",
    }

    # Flags that take their argument as a separate token.
    # Subclasses can extend this with toolchain-specific flags.
    SEPARATED_ARG_FLAGS: frozenset[str] = frozenset(
        [
            # Linker passthrough (when invoking cl.exe which calls link.exe)
            "/link",
        ]
    )

    def get_source_handler(self, suffix: str) -> SourceHandler | None:
        """Return handler for source file suffix.

        Handles C, C++, resource (.rc), and assembly (.asm) files with
        MSVC-compatible settings.
        """
        from pcons.tools.toolchain import SourceHandler

        suffix_lower = suffix.lower()
        if suffix_lower == ".c":
            return SourceHandler("cc", "c", ".obj", None, "msvc")
        if suffix_lower in (".cpp", ".cxx", ".cc", ".c++"):
            return SourceHandler("cxx", "cxx", ".obj", None, "msvc")
        # Handle .C as C++ (common convention, though case-insensitive on Windows)
        if suffix == ".C":
            return SourceHandler("cxx", "cxx", ".obj", None, "msvc")
        if suffix_lower == ".rc":
            # Resource files compile to .res and have no depfile
            return SourceHandler("rc", "resource", ".res", None, None, "rccmd")
        if suffix_lower == ".asm":
            # MASM assembly files - compiled with ml64.exe (x64) or ml.exe (x86)
            return SourceHandler("ml", "asm", ".obj", None, None, "asmcmd")
        return None

    def get_auxiliary_input_handler(self, suffix: str) -> AuxiliaryInputHandler | None:
        """Return handler for auxiliary input files.

        Handles .def (module definition) and .manifest files.
        Both MSVC and lld-link require /MANIFEST:EMBED with /MANIFESTINPUT.
        """
        from pcons.tools.toolchain import AuxiliaryInputHandler

        suffix_lower = suffix.lower()
        if suffix_lower == ".def":
            return AuxiliaryInputHandler(".def", "/DEF:$file")
        if suffix_lower == ".manifest":
            # Both MSVC and lld-link require /MANIFEST:EMBED with /MANIFESTINPUT
            return AuxiliaryInputHandler(
                ".manifest",
                "/MANIFESTINPUT:$file",
                extra_flags=["/MANIFEST:EMBED"],
            )
        return None

    def get_object_suffix(self) -> str:
        """Return the object file suffix (.obj for MSVC-compatible)."""
        return ".obj"

    def get_archiver_tool_name(self) -> str:
        """Return the archiver tool name (lib for MSVC-compatible)."""
        return "lib"

    def get_static_library_name(self, name: str) -> str:
        """Return filename for a static library (Windows-style .lib)."""
        return f"{name}.lib"

    def get_shared_library_name(self, name: str) -> str:
        """Return filename for a shared library (Windows-style .dll)."""
        return f"{name}.dll"

    def get_program_name(self, name: str) -> str:
        """Return filename for a program (Windows-style .exe)."""
        return f"{name}.exe"

    def get_compile_flags_for_target_type(self, target_type: str) -> list[str]:
        """Return additional compile flags for target type.

        MSVC-compatible toolchains don't need special flags like -fPIC.
        DLL exports are handled via __declspec or .def files.
        """
        return []

    def get_separated_arg_flags(self) -> frozenset[str]:
        """Return flags that take their argument as a separate token."""
        return self.SEPARATED_ARG_FLAGS

    def _apply_machine_flags(self, env: Environment, arch: str) -> None:
        """Add /MACHINE:xxx flags to linker and librarian.

        This is a helper method used by apply_target_arch implementations.
        """
        machine = self.MSVC_MACHINE_MAP.get(arch.lower(), arch.upper())

        if env.has_tool("link"):
            if isinstance(env.link.flags, list):
                env.link.flags.append(f"/MACHINE:{machine}")

        if env.has_tool("lib"):
            if isinstance(env.lib.flags, list):
                env.lib.flags.append(f"/MACHINE:{machine}")

    def apply_target_arch(self, env: Environment, arch: str, **kwargs: Any) -> None:
        """Apply target architecture flags.

        Adds /MACHINE:xxx to linker and librarian. Subclasses may override
        to add additional flags (e.g., clang-cl adds --target).
        """
        super().apply_target_arch(env, arch, **kwargs)
        self._apply_machine_flags(env, arch)

    def apply_preset(self, env: Environment, name: str) -> None:
        """Apply a named flag preset with MSVC-style flags.

        Args:
            env: Environment to modify.
            name: Preset name (warnings, sanitize, profile, lto, hardened).
        """
        preset = self.MSVC_PRESETS.get(name)
        if preset is None:
            logger.warning("Unknown preset '%s' for MSVC toolchain", name)
            return

        compile_flags = preset.get("compile_flags", [])
        link_flags = preset.get("link_flags", [])

        for tool_name in ("cc", "cxx"):
            if env.has_tool(tool_name):
                tool = getattr(env, tool_name)
                if hasattr(tool, "flags") and isinstance(tool.flags, list):
                    tool.flags.extend(compile_flags)

        if env.has_tool("link") and link_flags:
            if isinstance(env.link.flags, list):
                env.link.flags.extend(link_flags)

    def apply_cross_preset(self, env: Environment, preset: Any) -> None:
        """Apply a cross-compilation preset with MSVC-style flags.

        Applies /MACHINE:xxx flags via the architecture, then delegates
        to BaseToolchain for generic fields.

        Args:
            env: Environment to modify.
            preset: A CrossPreset dataclass instance.
        """
        # For MSVC, apply machine flags via architecture
        if hasattr(preset, "arch") and preset.arch:
            self._apply_machine_flags(env, preset.arch)

        # Apply extra compile/link flags and env_vars from base
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

        if hasattr(preset, "env_vars") and preset.env_vars:
            for var_name, value in preset.env_vars.items():
                tool_name = var_name.lower()
                if tool_name in ("cc", "cxx") and env.has_tool(tool_name):
                    getattr(env, tool_name).cmd = value

    def apply_variant(self, env: Environment, variant: str, **kwargs: Any) -> None:
        """Apply build variant (debug, release, etc.) with MSVC-style flags.

        Subclasses may override to add linker-specific flags.
        """
        super().apply_variant(env, variant, **kwargs)

        compile_flags: list[str] = []
        defines: list[str] = []

        variant_lower = variant.lower()
        if variant_lower == "debug":
            compile_flags = ["/Od", "/Zi"]
            defines = ["DEBUG", "_DEBUG"]
        elif variant_lower == "release":
            compile_flags = ["/O2"]
            defines = ["NDEBUG"]
        elif variant_lower == "relwithdebinfo":
            compile_flags = ["/O2", "/Zi"]
            defines = ["NDEBUG"]
        elif variant_lower == "minsizerel":
            compile_flags = ["/O1"]
            defines = ["NDEBUG"]
        else:
            logger.warning("Unknown variant '%s', no flags applied", variant)
            return

        # Add extra flags/defines from kwargs
        extra_flags = kwargs.get("extra_flags", [])
        extra_defines = kwargs.get("extra_defines", [])
        compile_flags.extend(extra_flags)
        defines.extend(extra_defines)

        for tool_name in ("cc", "cxx"):
            if env.has_tool(tool_name):
                tool = getattr(env, tool_name)
                if hasattr(tool, "flags") and isinstance(tool.flags, list):
                    tool.flags.extend(compile_flags)
                if hasattr(tool, "defines") and isinstance(tool.defines, list):
                    tool.defines.extend(defines)
