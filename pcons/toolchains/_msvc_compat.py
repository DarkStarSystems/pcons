# SPDX-License-Identifier: MIT
"""Shared base class for MSVC-compatible toolchains.

This module provides common functionality for toolchains that produce
MSVC-compatible binaries on Windows (MSVC and clang-cl).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pcons.core.preset import Preset, ToolContribution
from pcons.tools.toolchain import BaseToolchain

if TYPE_CHECKING:
    from pcons.tools.toolchain import AuxiliaryInputHandler, SourceHandler


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

    def get_compile_flags_for_target_type(self, target_type: str) -> list[str]:
        """Return additional compile flags for target type.

        MSVC-compatible toolchains don't need special flags like -fPIC.
        DLL exports are handled via __declspec or .def files.
        """
        return []

    def get_separated_arg_flags(self) -> frozenset[str]:
        """Return flags that take their argument as a separate token."""
        return self.SEPARATED_ARG_FLAGS

    # Variant flags per build type (compile_flags, defines).
    MSVC_VARIANTS: dict[str, tuple[list[str], list[str]]] = {
        "debug": (["/Od", "/Zi"], ["DEBUG", "_DEBUG"]),
        "release": (["/O2"], ["NDEBUG"]),
        "relwithdebinfo": (["/O2", "/Zi"], ["NDEBUG"]),
        "minsizerel": (["/O1"], ["NDEBUG"]),
    }

    def _arch_contributions(self, arch: str) -> list[ToolContribution]:
        """Add /MACHINE:xxx to linker and librarian."""
        machine = self.MSVC_MACHINE_MAP.get(arch.lower(), arch.upper())
        flag = f"/MACHINE:{machine}"
        return [
            ToolContribution("link", flags=(flag,)),
            ToolContribution("lib", flags=(flag,)),
        ]

    def make_feature_preset(self, name: str) -> Preset | None:
        spec = self.MSVC_PRESETS.get(name)
        if spec is None:
            return None
        contribs: list[ToolContribution] = []
        compile_flags = spec.get("compile_flags", [])
        if compile_flags:
            contribs.append(ToolContribution("cc", flags=tuple(compile_flags)))
            contribs.append(ToolContribution("cxx", flags=tuple(compile_flags)))
        link_flags = spec.get("link_flags", [])
        if link_flags:
            contribs.append(ToolContribution("link", flags=tuple(link_flags)))
        return Preset(name=name, category="feature", contributions=tuple(contribs))

    def _variant_contributions(
        self, variant: str, **kwargs: Any
    ) -> list[ToolContribution]:
        spec = self.MSVC_VARIANTS.get(variant.lower())
        if spec is None:
            raise ValueError(
                f"Unknown variant '{variant}'. "
                f"Supported variants: debug, release, relwithdebinfo, minsizerel."
            )
        flags = list(spec[0]) + list(kwargs.get("extra_flags", []))
        defines = list(spec[1]) + list(kwargs.get("extra_defines", []))
        return [
            ToolContribution("cc", flags=tuple(flags), defines=tuple(defines)),
            ToolContribution("cxx", flags=tuple(flags), defines=tuple(defines)),
        ]
