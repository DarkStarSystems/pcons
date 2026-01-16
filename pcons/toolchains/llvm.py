# SPDX-License-Identifier: MIT
"""LLVM/Clang toolchain implementation.

Provides LLVM-based C and C++ compilation toolchain including:
- Clang C compiler (clang)
- Clang C++ compiler (clang++)
- LLVM archiver (llvm-ar or ar)
- Linker (using clang/clang++ or lld)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pcons.configure.platform import get_platform
from pcons.core.builder import CommandBuilder
from pcons.tools.tool import BaseTool
from pcons.tools.toolchain import BaseToolchain

if TYPE_CHECKING:
    from pcons.core.builder import Builder
    from pcons.core.toolconfig import ToolConfig


class ClangCCompiler(BaseTool):
    """Clang C compiler tool.

    Provides the 'cc' tool for compiling C source files to object files.

    Variables:
        cmd: Compiler command (default: 'clang')
        flags: Compiler flags
        includes: Include directories (-I flags)
        defines: Preprocessor definitions (-D flags)
        depflags: Dependency generation flags
        objcmd: Command template for compiling to object
    """

    def __init__(self) -> None:
        super().__init__("cc", language="c")

    def default_vars(self) -> dict[str, object]:
        return {
            "cmd": "clang",
            "flags": [],
            "includes": [],
            "defines": [],
            "depflags": "-MD -MF $out.d",
            "objcmd": "$cc.cmd $cc.flags $cc.includes $cc.defines $cc.depflags -c -o $out $in",
        }

    def builders(self) -> dict[str, Builder]:
        platform = get_platform()
        return {
            "Object": CommandBuilder(
                "Object",
                "cc",
                "objcmd",
                src_suffixes=[".c"],
                target_suffixes=[platform.object_suffix],
                language="c",
                single_source=True,
            ),
        }

    def configure(self, config: object) -> ToolConfig | None:
        """Detect Clang C compiler."""
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
    """Clang C++ compiler tool.

    Provides the 'cxx' tool for compiling C++ source files to object files.

    Variables:
        cmd: Compiler command (default: 'clang++')
        flags: Compiler flags
        includes: Include directories (-I flags)
        defines: Preprocessor definitions (-D flags)
        depflags: Dependency generation flags
        objcmd: Command template for compiling to object
    """

    def __init__(self) -> None:
        super().__init__("cxx", language="cxx")

    def default_vars(self) -> dict[str, object]:
        return {
            "cmd": "clang++",
            "flags": [],
            "includes": [],
            "defines": [],
            "depflags": "-MD -MF $out.d",
            "objcmd": "$cxx.cmd $cxx.flags $cxx.includes $cxx.defines $cxx.depflags -c -o $out $in",
        }

    def builders(self) -> dict[str, Builder]:
        platform = get_platform()
        return {
            "Object": CommandBuilder(
                "Object",
                "cxx",
                "objcmd",
                src_suffixes=[".cpp", ".cxx", ".cc", ".C"],
                target_suffixes=[platform.object_suffix],
                language="cxx",
                single_source=True,
            ),
        }

    def configure(self, config: object) -> ToolConfig | None:
        """Detect Clang C++ compiler."""
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
    """LLVM archiver tool.

    Provides the 'ar' tool for creating static libraries.
    Uses llvm-ar if available, falls back to system ar.

    Variables:
        cmd: Archiver command (default: 'llvm-ar' or 'ar')
        flags: Archiver flags (default: 'rcs')
        libcmd: Command template for creating static library
    """

    def __init__(self) -> None:
        super().__init__("ar")

    def default_vars(self) -> dict[str, object]:
        return {
            "cmd": "llvm-ar",
            "flags": "rcs",
            "libcmd": "$ar.cmd $ar.flags $out $in",
        }

    def builders(self) -> dict[str, Builder]:
        platform = get_platform()
        return {
            "StaticLibrary": CommandBuilder(
                "StaticLibrary",
                "ar",
                "libcmd",
                src_suffixes=[platform.object_suffix],
                target_suffixes=[platform.static_lib_suffix],
                single_source=False,
            ),
        }

    def configure(self, config: object) -> ToolConfig | None:
        """Detect LLVM archiver."""
        from pcons.configure.config import Configure
        if not isinstance(config, Configure):
            return None

        # Prefer llvm-ar, fall back to ar
        ar = config.find_program("llvm-ar")
        if ar is None:
            ar = config.find_program("ar")

        if ar is None:
            return None

        from pcons.core.toolconfig import ToolConfig
        tool_config = ToolConfig("ar", cmd=str(ar.path))
        return tool_config


class LlvmLinker(BaseTool):
    """LLVM linker tool.

    Provides the 'link' tool for linking object files into executables
    or shared libraries. Uses clang/clang++ as the linker driver.

    Variables:
        cmd: Linker command (default: 'clang')
        flags: Linker flags
        libs: Libraries to link (-l flags)
        libdirs: Library directories (-L flags)
        progcmd: Command template for linking program
        sharedcmd: Command template for linking shared library
    """

    def __init__(self) -> None:
        super().__init__("link")

    def default_vars(self) -> dict[str, object]:
        platform = get_platform()
        shared_flag = "-shared" if not platform.is_macos else "-dynamiclib"
        return {
            "cmd": "clang",
            "flags": [],
            "libs": [],
            "libdirs": [],
            "progcmd": "$link.cmd $link.flags -o $out $in $link.libdirs $link.libs",
            "sharedcmd": f"$link.cmd {shared_flag} $link.flags -o $out $in $link.libdirs $link.libs",
        }

    def builders(self) -> dict[str, Builder]:
        platform = get_platform()
        return {
            "Program": CommandBuilder(
                "Program",
                "link",
                "progcmd",
                src_suffixes=[platform.object_suffix],
                target_suffixes=[platform.exe_suffix],
                single_source=False,
            ),
            "SharedLibrary": CommandBuilder(
                "SharedLibrary",
                "link",
                "sharedcmd",
                src_suffixes=[platform.object_suffix],
                target_suffixes=[platform.shared_lib_suffix],
                single_source=False,
            ),
        }

    def configure(self, config: object) -> ToolConfig | None:
        """Detect linker (same as C compiler)."""
        from pcons.configure.config import Configure
        if not isinstance(config, Configure):
            return None

        clang = config.find_program("clang")
        if clang is None:
            return None

        from pcons.core.toolconfig import ToolConfig
        tool_config = ToolConfig("link", cmd=str(clang.path))
        return tool_config


class LlvmToolchain(BaseToolchain):
    """LLVM/Clang toolchain.

    A complete LLVM-based toolchain for C and C++ development.
    Includes:
    - C compiler (clang)
    - C++ compiler (clang++)
    - Archiver (llvm-ar or ar)
    - Linker (clang/clang++)

    Example:
        config = Configure()
        llvm = LlvmToolchain()
        if llvm.configure(config):
            env = project.Environment(toolchain=llvm)
            env.cc.Object("main.o", "main.c")
    """

    def __init__(self) -> None:
        super().__init__("llvm")

    def _configure_tools(self, config: object) -> bool:
        """Configure all LLVM tools."""
        from pcons.configure.config import Configure
        if not isinstance(config, Configure):
            return False

        # Try to configure each tool
        cc = ClangCCompiler()
        cc_config = cc.configure(config)
        if cc_config is None:
            return False

        cxx = ClangCxxCompiler()
        cxx.configure(config)  # C++ is optional

        ar = LlvmArchiver()
        ar.configure(config)  # Archiver is optional

        link = LlvmLinker()
        link_config = link.configure(config)
        if link_config is None:
            return False

        # Store configured tools
        self._tools = {
            "cc": cc,
            "cxx": cxx,
            "ar": ar,
            "link": link,
        }

        return True
