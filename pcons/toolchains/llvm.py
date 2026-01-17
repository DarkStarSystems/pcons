# SPDX-License-Identifier: MIT
"""LLVM/Clang toolchain implementation."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pcons.configure.platform import get_platform
from pcons.core.builder import CommandBuilder, MultiOutputBuilder, OutputSpec
from pcons.tools.tool import BaseTool
from pcons.tools.toolchain import BaseToolchain

if TYPE_CHECKING:
    from pcons.core.builder import Builder
    from pcons.core.environment import Environment
    from pcons.core.toolconfig import ToolConfig
    from pcons.tools.toolchain import SourceHandler


class ClangCCompiler(BaseTool):
    """Clang C compiler tool."""

    def __init__(self) -> None:
        super().__init__("cc", language="c")

    def default_vars(self) -> dict[str, object]:
        return {
            "cmd": "clang",
            "flags": [],
            "iprefix": "-I",
            "includes": [],
            "dprefix": "-D",
            "defines": [],
            "depflags": ["-MD", "-MF", "$$out.d"],
            "objcmd": [
                "$cc.cmd",
                "$cc.flags",
                "${prefix(cc.iprefix, cc.includes)}",
                "${prefix(cc.dprefix, cc.defines)}",
                "$cc.depflags",
                "-c", "-o", "$$out", "$$in",
            ],
        }

    def builders(self) -> dict[str, Builder]:
        platform = get_platform()
        return {
            "Object": CommandBuilder(
                "Object", "cc", "objcmd",
                src_suffixes=[".c"],
                target_suffixes=[platform.object_suffix],
                language="c",
                single_source=True,
                depfile="$out.d",
                deps_style="gcc",
            ),
        }

    def configure(self, config: object) -> ToolConfig | None:
        from pcons.configure.config import Configure
        if not isinstance(config, Configure):
            return None
        clang = config.find_program("clang")
        if clang is None:
            return None
        from pcons.core.toolconfig import ToolConfig
        tool_config = ToolConfig("cc", cmd=str(clang.path))
        if clang.version:
            tool_config.version = clang.version
        return tool_config


class ClangCxxCompiler(BaseTool):
    """Clang C++ compiler tool."""

    def __init__(self) -> None:
        super().__init__("cxx", language="cxx")

    def default_vars(self) -> dict[str, object]:
        return {
            "cmd": "clang++",
            "flags": [],
            "iprefix": "-I",
            "includes": [],
            "dprefix": "-D",
            "defines": [],
            "depflags": ["-MD", "-MF", "$$out.d"],
            "objcmd": [
                "$cxx.cmd",
                "$cxx.flags",
                "${prefix(cxx.iprefix, cxx.includes)}",
                "${prefix(cxx.dprefix, cxx.defines)}",
                "$cxx.depflags",
                "-c", "-o", "$$out", "$$in",
            ],
        }

    def builders(self) -> dict[str, Builder]:
        platform = get_platform()
        return {
            "Object": CommandBuilder(
                "Object", "cxx", "objcmd",
                src_suffixes=[".cpp", ".cxx", ".cc", ".C"],
                target_suffixes=[platform.object_suffix],
                language="cxx",
                single_source=True,
                depfile="$out.d",
                deps_style="gcc",
            ),
        }

    def configure(self, config: object) -> ToolConfig | None:
        from pcons.configure.config import Configure
        if not isinstance(config, Configure):
            return None
        clangxx = config.find_program("clang++")
        if clangxx is None:
            return None
        from pcons.core.toolconfig import ToolConfig
        tool_config = ToolConfig("cxx", cmd=str(clangxx.path))
        if clangxx.version:
            tool_config.version = clangxx.version
        return tool_config


class LlvmArchiver(BaseTool):
    """LLVM archiver tool."""

    def __init__(self) -> None:
        super().__init__("ar")

    def default_vars(self) -> dict[str, object]:
        return {
            "cmd": "llvm-ar",
            "flags": ["rcs"],
            "libcmd": ["$ar.cmd", "$ar.flags", "$$out", "$$in"],
        }

    def builders(self) -> dict[str, Builder]:
        platform = get_platform()
        return {
            "StaticLibrary": CommandBuilder(
                "StaticLibrary", "ar", "libcmd",
                src_suffixes=[platform.object_suffix],
                target_suffixes=[platform.static_lib_suffix],
                single_source=False,
            ),
        }

    def configure(self, config: object) -> ToolConfig | None:
        from pcons.configure.config import Configure
        if not isinstance(config, Configure):
            return None
        ar = config.find_program("llvm-ar")
        if ar is None:
            ar = config.find_program("ar")
        if ar is None:
            return None
        from pcons.core.toolconfig import ToolConfig
        return ToolConfig("ar", cmd=str(ar.path))


class LlvmLinker(BaseTool):
    """LLVM linker tool."""

    def __init__(self) -> None:
        super().__init__("link")

    def default_vars(self) -> dict[str, object]:
        platform = get_platform()
        shared_flag = "-dynamiclib" if platform.is_macos else "-shared"
        return {
            "cmd": "clang",
            "flags": [],
            "lprefix": "-l",
            "libs": [],
            "Lprefix": "-L",
            "libdirs": [],
            "progcmd": [
                "$link.cmd", "$link.flags",
                "-o", "$$out", "$$in",
                "${prefix(link.Lprefix, link.libdirs)}",
                "${prefix(link.lprefix, link.libs)}",
            ],
            "sharedcmd": [
                "$link.cmd", shared_flag, "$link.flags",
                "-o", "$$out", "$$in",
                "${prefix(link.Lprefix, link.libdirs)}",
                "${prefix(link.lprefix, link.libs)}",
            ],
        }

    def builders(self) -> dict[str, Builder]:
        platform = get_platform()
        return {
            "Program": CommandBuilder(
                "Program", "link", "progcmd",
                src_suffixes=[platform.object_suffix],
                target_suffixes=[platform.exe_suffix],
                single_source=False,
            ),
            "SharedLibrary": MultiOutputBuilder(
                "SharedLibrary", "link", "sharedcmd",
                outputs=[
                    OutputSpec("primary", platform.shared_lib_suffix),
                ],
                src_suffixes=[platform.object_suffix],
                single_source=False,
            ),
        }

    def configure(self, config: object) -> ToolConfig | None:
        from pcons.configure.config import Configure
        if not isinstance(config, Configure):
            return None
        clang = config.find_program("clang")
        if clang is None:
            return None
        from pcons.core.toolconfig import ToolConfig
        return ToolConfig("link", cmd=str(clang.path))


class LlvmToolchain(BaseToolchain):
    """LLVM/Clang toolchain for C and C++ development."""

    def __init__(self) -> None:
        super().__init__("llvm")

    # =========================================================================
    # Source Handler Methods
    # =========================================================================

    def get_source_handler(self, suffix: str) -> "SourceHandler | None":
        """Return handler for source file suffix, or None if not handled."""
        from pcons.tools.toolchain import SourceHandler

        suffix_lower = suffix.lower()
        if suffix_lower == ".c":
            return SourceHandler("cc", "c", ".o", "$out.d", "gcc")
        if suffix_lower in (".cpp", ".cxx", ".cc", ".c++"):
            return SourceHandler("cxx", "cxx", ".o", "$out.d", "gcc")
        # Handle case-sensitive .C (C++ on Unix)
        if suffix == ".C":
            return SourceHandler("cxx", "cxx", ".o", "$out.d", "gcc")
        # Objective-C
        if suffix_lower == ".m":
            return SourceHandler("cc", "objc", ".o", "$out.d", "gcc")
        if suffix_lower == ".mm":
            return SourceHandler("cxx", "objcxx", ".o", "$out.d", "gcc")
        return None

    def get_object_suffix(self) -> str:
        """Return the object file suffix for LLVM toolchain."""
        return ".o"

    def get_static_library_name(self, name: str) -> str:
        """Return filename for a static library (Unix-style)."""
        return f"lib{name}.a"

    def get_shared_library_name(self, name: str) -> str:
        """Return filename for a shared library (platform-aware)."""
        platform = get_platform()
        if platform.is_macos:
            return f"lib{name}.dylib"
        return f"lib{name}.so"

    def get_program_name(self, name: str) -> str:
        """Return filename for a program (no suffix on Unix)."""
        return name

    def _configure_tools(self, config: object) -> bool:
        from pcons.configure.config import Configure
        if not isinstance(config, Configure):
            return False

        cc = ClangCCompiler()
        if cc.configure(config) is None:
            return False

        cxx = ClangCxxCompiler()
        cxx.configure(config)

        ar = LlvmArchiver()
        ar.configure(config)

        link = LlvmLinker()
        if link.configure(config) is None:
            return False

        self._tools = {"cc": cc, "cxx": cxx, "ar": ar, "link": link}
        return True

    def apply_variant(self, env: Environment, variant: str, **kwargs: Any) -> None:
        """Apply build variant (debug, release, etc.)."""
        super().apply_variant(env, variant, **kwargs)

        compile_flags: list[str] = []
        defines: list[str] = []

        variant_lower = variant.lower()
        if variant_lower == "debug":
            compile_flags = ["-O0", "-g"]
            defines = ["DEBUG", "_DEBUG"]
        elif variant_lower == "release":
            compile_flags = ["-O2"]
            defines = ["NDEBUG"]
        elif variant_lower == "relwithdebinfo":
            compile_flags = ["-O2", "-g"]
            defines = ["NDEBUG"]
        elif variant_lower == "minsizerel":
            compile_flags = ["-Os"]
            defines = ["NDEBUG"]

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

from pcons.tools.toolchain import toolchain_registry

toolchain_registry.register(
    LlvmToolchain,
    aliases=["llvm", "clang"],
    check_command="clang",
    tool_classes=[ClangCCompiler, ClangCxxCompiler, LlvmArchiver, LlvmLinker],
    category="c",
)
