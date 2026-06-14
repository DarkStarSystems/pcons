# SPDX-License-Identifier: MIT
"""Clang-CL toolchain implementation.

Clang-CL is LLVM's MSVC-compatible compiler driver for Windows.
It uses MSVC-style command-line flags and produces MSVC-compatible binaries,
making it a drop-in replacement for cl.exe while providing Clang's
diagnostics and optimizations.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from pcons.configure.platform import get_platform
from pcons.core.builder import CommandBuilder, MultiOutputBuilder, OutputSpec
from pcons.core.preset import ToolContribution
from pcons.core.subst import SourcePath, TargetPath
from pcons.toolchains._msvc_compat import MsvcCompatibleToolchain
from pcons.toolchains.msvc import MsvcAssembler, MsvcResourceCompiler
from pcons.tools.tool import BaseTool

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from pcons.core.builder import Builder
    from pcons.core.toolconfig import ToolConfig


class ClangClCompiler(BaseTool):
    """Clang-CL C/C++ compiler tool.

    Uses MSVC-compatible flags like /O2, /W4, /I, /D, etc.
    """

    def __init__(self, name: str = "cc", language: str = "c") -> None:
        super().__init__(name, language=language)
        self._language = language

    def default_vars(self) -> dict[str, object]:
        return {
            "cmd": "clang-cl",
            "flags": ["/nologo"],
            "iprefix": "/I",
            "includes": [],
            "dprefix": "/D",
            "defines": [],
            # clang-cl uses /showIncludes for MSVC-style deps
            "objcmd": [
                "$cc.cmd" if self._language == "c" else "$cxx.cmd",
                "/nologo",
                "/showIncludes",
                "/c",
                TargetPath(prefix="/Fo"),
                "${prefix(cc.iprefix, cc.includes)}"
                if self._language == "c"
                else "${prefix(cxx.iprefix, cxx.includes)}",
                "${prefix(cc.dprefix, cc.defines)}"
                if self._language == "c"
                else "${prefix(cxx.dprefix, cxx.defines)}",
                "$cc.flags" if self._language == "c" else "$cxx.flags",
                SourcePath(),
            ],
        }

    def builders(self) -> dict[str, Builder]:
        suffixes = [".c"] if self._language == "c" else [".cpp", ".cxx", ".cc", ".C"]
        return {
            "Object": CommandBuilder(
                "Object",
                self.name,
                "objcmd",
                src_suffixes=suffixes,
                target_suffixes=[".obj"],
                language=self._language,
                single_source=True,
                deps_style="msvc",
            ),
        }

    def configure(self, config: object) -> ToolConfig | None:
        if not get_platform().is_windows:
            return None
        return self._find_tool_config(config, "clang-cl", with_version=True)


class ClangClCCompiler(ClangClCompiler):
    """Clang-CL C compiler."""

    def __init__(self) -> None:
        super().__init__("cc", "c")


class ClangClCxxCompiler(ClangClCompiler):
    """Clang-CL C++ compiler."""

    def __init__(self) -> None:
        super().__init__("cxx", "cxx")

    def default_vars(self) -> dict[str, object]:
        return {
            "cmd": "clang-cl",
            "flags": ["/nologo"],
            "iprefix": "/I",
            "includes": [],
            "dprefix": "/D",
            "defines": [],
            "objcmd": [
                "$cxx.cmd",
                "/nologo",
                "/showIncludes",
                "/c",
                TargetPath(prefix="/Fo"),
                "${prefix(cxx.iprefix, cxx.includes)}",
                "${prefix(cxx.dprefix, cxx.defines)}",
                "$cxx.flags",
                SourcePath(),
            ],
        }


class ClangClLibrarian(BaseTool):
    """Librarian for clang-cl (uses llvm-lib or lib.exe)."""

    def __init__(self) -> None:
        super().__init__("lib")

    def default_vars(self) -> dict[str, object]:
        return {
            "cmd": "llvm-lib",
            "flags": ["/nologo"],
            "libcmd": [
                "$lib.cmd",
                "$lib.flags",
                TargetPath(prefix="/OUT:"),
                SourcePath(),
            ],
        }

    def builders(self) -> dict[str, Builder]:
        return {
            "StaticLibrary": CommandBuilder(
                "StaticLibrary",
                "lib",
                "libcmd",
                src_suffixes=[".obj"],
                target_suffixes=[".lib"],
                single_source=False,
            ),
        }

    def configure(self, config: object) -> ToolConfig | None:
        if not get_platform().is_windows:
            return None
        # Prefer llvm-lib, fall back to lib.exe
        return self._find_tool_config(config, "llvm-lib", "lib.exe", version_flag="")


class ClangClLinker(BaseTool):
    """Linker for clang-cl (uses lld-link or link.exe)."""

    def __init__(self) -> None:
        super().__init__("link")

    def default_vars(self) -> dict[str, object]:
        return {
            "cmd": "lld-link",
            "flags": ["/nologo"],
            "lprefix": "",
            "libs": [],
            "Lprefix": "/LIBPATH:",
            "libdirs": [],
            "progcmd": [
                "$link.cmd",
                "$link.flags",
                TargetPath(prefix="/OUT:"),
                SourcePath(),
                "${prefix(link.Lprefix, link.libdirs)}",
                "$link.libs",
            ],
            "sharedcmd": [
                "$link.cmd",
                "/DLL",
                "$link.flags",
                TargetPath(prefix="/OUT:", index=0),
                TargetPath(prefix="/IMPLIB:", index=1),
                SourcePath(),
                "${prefix(link.Lprefix, link.libdirs)}",
                "$link.libs",
            ],
        }

    def builders(self) -> dict[str, Builder]:
        return {
            "Program": CommandBuilder(
                "Program",
                "link",
                "progcmd",
                src_suffixes=[".obj", ".lib", ".res"],
                target_suffixes=[".exe"],
                single_source=False,
            ),
            "SharedLibrary": MultiOutputBuilder(
                "SharedLibrary",
                "link",
                "sharedcmd",
                outputs=[
                    OutputSpec("primary", ".dll"),
                    OutputSpec("import_lib", ".lib"),
                ],
                src_suffixes=[".obj", ".lib"],
                single_source=False,
            ),
        }

    def configure(self, config: object) -> ToolConfig | None:
        if not get_platform().is_windows:
            return None
        # Prefer lld-link, fall back to link.exe
        return self._find_tool_config(config, "lld-link", "link.exe", version_flag="")


class ClangClToolchain(MsvcCompatibleToolchain):
    """Clang-CL toolchain for Windows development.

    Uses clang-cl with MSVC-compatible flags, producing binaries
    compatible with the MSVC ecosystem. Inherits common MSVC-compatible
    functionality from MsvcCompatibleToolchain.
    """

    TOOL_NAMES = ("cc", "cxx", "lib", "link", "rc", "ml")

    # Clang-CL supports additional GCC-style flags beyond the base MSVC set
    SEPARATED_ARG_FLAGS: frozenset[str] = frozenset(
        [
            # MSVC-style linker passthrough
            "/link",
            # GCC-style flags that clang-cl also supports
            "-target",
            "--target",
            "-Xlinker",
        ]
    )

    def __init__(self) -> None:
        super().__init__("clang-cl")

    def _configure_tools(self, config: object) -> bool:
        from pcons.configure.config import Configure

        if not isinstance(config, Configure):
            return False
        platform = get_platform()
        if not platform.is_windows:
            return False

        cc = ClangClCCompiler()
        if cc.configure(config) is None:
            return False

        cxx = ClangClCxxCompiler()
        cxx.configure(config)

        lib = ClangClLibrarian()
        if lib.configure(config) is None:
            return False

        link = ClangClLinker()
        if link.configure(config) is None:
            return False

        rc = MsvcResourceCompiler()
        rc.configure(config)  # Optional - not required for toolchain to work

        ml = MsvcAssembler()
        ml.configure(config)  # Optional - not required for toolchain to work

        self._tools = {
            "cc": cc,
            "cxx": cxx,
            "lib": lib,
            "link": link,
            "rc": rc,
            "ml": ml,
        }
        return True

    # Architecture to Clang target triple mapping for Windows
    CLANG_CL_TARGET_MAP: dict[str, str] = {
        "x64": "x86_64-pc-windows-msvc",
        "x86": "i686-pc-windows-msvc",
        "arm64": "aarch64-pc-windows-msvc",
        # Common aliases
        "amd64": "x86_64-pc-windows-msvc",
        "x86_64": "x86_64-pc-windows-msvc",
        "i386": "i686-pc-windows-msvc",
        "i686": "i686-pc-windows-msvc",
        "aarch64": "aarch64-pc-windows-msvc",
    }

    def _arch_contributions(self, arch: str) -> list[ToolContribution]:
        """Add /MACHINE:xxx (via base) plus --target for cross-compilation.

        Supported architectures:
        - x64 (or amd64, x86_64): 64-bit Intel/AMD
        - x86 (or i386, i686): 32-bit Intel/AMD
        - arm64 (or aarch64): 64-bit ARM
        """
        contribs = super()._arch_contributions(arch)
        target_triple = self.CLANG_CL_TARGET_MAP.get(arch.lower())
        if target_triple:
            flag = f"--target={target_triple}"
            contribs.append(ToolContribution("cc", flags=(flag,)))
            contribs.append(ToolContribution("cxx", flags=(flag,)))
        return contribs


# =============================================================================
# Registration
# =============================================================================

from pcons.tools.toolchain import toolchain_registry  # noqa: E402

toolchain_registry.register(
    ClangClToolchain,
    aliases=["clang-cl"],
    check_command="clang-cl",
    tool_classes=[
        ClangClCCompiler,
        ClangClCxxCompiler,
        ClangClLibrarian,
        ClangClLinker,
        MsvcResourceCompiler,
        MsvcAssembler,
    ],
    category="c",
    platforms=["win32"],
    description="Clang with MSVC-compatible flags",
    finder="find_c_toolchain()",
)
