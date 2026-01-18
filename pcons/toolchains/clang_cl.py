# SPDX-License-Identifier: MIT
"""Clang-CL toolchain implementation.

Clang-CL is LLVM's MSVC-compatible compiler driver for Windows.
It uses MSVC-style command-line flags and produces MSVC-compatible binaries,
making it a drop-in replacement for cl.exe while providing Clang's
diagnostics and optimizations.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pcons.configure.platform import get_platform
from pcons.core.builder import CommandBuilder, MultiOutputBuilder, OutputSpec
from pcons.toolchains.msvc import MsvcAssembler
from pcons.tools.tool import BaseTool
from pcons.tools.toolchain import BaseToolchain

if TYPE_CHECKING:
    from pcons.core.builder import Builder
    from pcons.core.environment import Environment
    from pcons.core.toolconfig import ToolConfig
    from pcons.tools.toolchain import AuxiliaryInputHandler, SourceHandler


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
                "/Fo$$out",
                "${prefix(cc.iprefix, cc.includes)}"
                if self._language == "c"
                else "${prefix(cxx.iprefix, cxx.includes)}",
                "${prefix(cc.dprefix, cc.defines)}"
                if self._language == "c"
                else "${prefix(cxx.dprefix, cxx.defines)}",
                "$cc.flags" if self._language == "c" else "$cxx.flags",
                "$$in",
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
        from pcons.configure.config import Configure

        if not isinstance(config, Configure):
            return None
        platform = get_platform()
        if not platform.is_windows:
            return None
        clang_cl = config.find_program("clang-cl")
        if clang_cl is None:
            return None
        from pcons.core.toolconfig import ToolConfig

        tool_config = ToolConfig(self.name, cmd=str(clang_cl.path))
        if clang_cl.version:
            tool_config.version = clang_cl.version
        return tool_config


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
                "/Fo$$out",
                "${prefix(cxx.iprefix, cxx.includes)}",
                "${prefix(cxx.dprefix, cxx.defines)}",
                "$cxx.flags",
                "$$in",
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
            "libcmd": ["$lib.cmd", "$lib.flags", "/OUT:$$out", "$$in"],
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
        from pcons.configure.config import Configure

        if not isinstance(config, Configure):
            return None
        platform = get_platform()
        if not platform.is_windows:
            return None
        # Prefer llvm-lib, fall back to lib.exe
        lib = config.find_program("llvm-lib", version_flag="")
        if lib is None:
            lib = config.find_program("lib.exe", version_flag="")
        if lib is None:
            return None
        from pcons.core.toolconfig import ToolConfig

        return ToolConfig("lib", cmd=str(lib.path))


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
                "/OUT:$$out",
                "$$in",
                "${prefix(link.Lprefix, link.libdirs)}",
                "$link.libs",
            ],
            "sharedcmd": [
                "$link.cmd",
                "/DLL",
                "$link.flags",
                "/OUT:$$out",
                "$$in",
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
        from pcons.configure.config import Configure

        if not isinstance(config, Configure):
            return None
        platform = get_platform()
        if not platform.is_windows:
            return None
        # Prefer lld-link, fall back to link.exe
        link = config.find_program("lld-link", version_flag="")
        if link is None:
            link = config.find_program("link.exe", version_flag="")
        if link is None:
            return None
        from pcons.core.toolconfig import ToolConfig

        return ToolConfig("link", cmd=str(link.path))


class ClangClToolchain(BaseToolchain):
    """Clang-CL toolchain for Windows development.

    Uses clang-cl with MSVC-compatible flags, producing binaries
    compatible with the MSVC ecosystem.
    """

    def __init__(self) -> None:
        super().__init__("clang-cl")

    def get_source_handler(self, suffix: str) -> SourceHandler | None:
        """Return handler for source file suffix."""
        from pcons.tools.toolchain import SourceHandler

        suffix_lower = suffix.lower()
        if suffix_lower == ".c":
            return SourceHandler("cc", "c", ".obj", None, "msvc")
        if suffix_lower in (".cpp", ".cxx", ".cc", ".c++"):
            return SourceHandler("cxx", "cxx", ".obj", None, "msvc")
        if suffix == ".C":
            return SourceHandler("cxx", "cxx", ".obj", None, "msvc")
        if suffix_lower == ".asm":
            # MASM assembly files - compiled with ml64.exe (x64) or ml.exe (x86)
            # Uses the same assembler as MSVC
            return SourceHandler("ml", "asm", ".obj", None, None, "asmcmd")
        return None

    def get_auxiliary_input_handler(self, suffix: str) -> AuxiliaryInputHandler | None:
        """Return handler for auxiliary input files."""
        from pcons.tools.toolchain import AuxiliaryInputHandler

        # Clang-CL uses the same linker flags as MSVC
        if suffix.lower() == ".def":
            return AuxiliaryInputHandler(".def", "/DEF:$file")
        return None

    def get_object_suffix(self) -> str:
        """Return the object file suffix for clang-cl."""
        return ".obj"

    def get_archiver_tool_name(self) -> str:
        """Return the archiver tool name (uses lib like MSVC)."""
        return "lib"

    def get_static_library_name(self, name: str) -> str:
        """Return filename for a static library (Windows-style)."""
        return f"{name}.lib"

    def get_shared_library_name(self, name: str) -> str:
        """Return filename for a shared library (DLL)."""
        return f"{name}.dll"

    def get_program_name(self, name: str) -> str:
        """Return filename for a program (with .exe suffix)."""
        return f"{name}.exe"

    def get_compile_flags_for_target_type(self, target_type: str) -> list[str]:
        """Return additional compile flags for target type.

        Clang-CL doesn't need special flags like -fPIC on Windows.
        """
        return []

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

        ml = MsvcAssembler()
        ml.configure(config)  # Optional - not required for toolchain to work

        self._tools = {"cc": cc, "cxx": cxx, "lib": lib, "link": link, "ml": ml}
        return True

    def apply_variant(self, env: Environment, variant: str, **kwargs: Any) -> None:
        """Apply build variant (debug, release, etc.)."""
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
        MsvcAssembler,
    ],
    category="c",
)
