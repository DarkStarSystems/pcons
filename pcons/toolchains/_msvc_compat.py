# SPDX-License-Identifier: MIT
"""Shared base class for MSVC-compatible toolchains (MSVC and clang-cl)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pcons.core.preset import ToolContribution
from pcons.tools.toolchain import BaseToolchain

if TYPE_CHECKING:
    from pcons.core.environment import Environment
    from pcons.toolchains.build_context import CompileLinkContext
    from pcons.tools.toolchain import AuxiliaryInputHandler, SourceHandler


class MsvcCompatibleToolchain(BaseToolchain):
    """Base class for MSVC-compatible toolchains (MSVC and clang-cl).

    Shares source/auxiliary-input handling, output naming, /MACHINE:
    arch flags, and build variants; subclasses override where the two
    toolchains differ.
    """

    # Named feature presets; see docs/presets.md.
    FEATURE_PRESETS: dict[str, dict[str, list[str]]] = {
        "warnings": {
            "compile_flags": ["/W4"],
        },
        "werror": {
            "compile_flags": ["/WX"],
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

    def compile_link_context_class(self) -> type[CompileLinkContext]:
        """MSVC and clang-cl both use MSVC-style command formatting.

        This selects MsvcCompileLinkContext, which formats library names as
        ``foo.lib`` (rather than the bare ``foo`` of the GNU-style base).
        Without it, clang-cl's linker fails with "could not open 'foo'" on
        any library referenced by name (e.g. from an imported package).
        """
        from pcons.toolchains.build_context import MsvcCompileLinkContext

        return MsvcCompileLinkContext

    def get_source_handler(self, suffix: str) -> SourceHandler | None:
        """Return handler for C, C++, resource (.rc), or assembly (.asm) files."""
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
        """Return handler for .def (module definition) and .manifest files."""
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

    def _cxx_standard_flag(self, standard: int) -> str:
        # MSVC switches are /std:c++14, c++17, c++20, and c++latest — there is
        # no /std:c++23 or :c++26, so anything above 20 uses c++latest.
        if standard >= 23:
            return "/std:c++latest"
        if standard <= 14:
            return "/std:c++14"
        return f"/std:c++{standard}"

    def apply_cross_preset(self, env: Environment, preset: Any) -> None:
        """Raise: these toolchains target only Windows; CPU selection is
        set_target_arch's job."""
        name = getattr(preset, "name", preset)
        raise ValueError(
            f"Cross preset '{name}' does not apply to the {self.name} toolchain: "
            f"it targets only Windows. To select the CPU, use "
            f'env.set_target_arch(...) (e.g. "arm64") instead.'
        )

    def _arch_contributions(self, arch: str) -> list[ToolContribution]:
        """Add /MACHINE:xxx to linker and librarian."""
        machine = self.MSVC_MACHINE_MAP.get(arch.lower(), arch.upper())
        flag = f"/MACHINE:{machine}"
        return [
            ToolContribution("link", flags=(flag,)),
            ToolContribution("lib", flags=(flag,)),
        ]

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
