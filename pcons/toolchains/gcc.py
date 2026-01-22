# SPDX-License-Identifier: MIT
"""GCC toolchain implementation.

Provides GCC-based C and C++ compilation toolchain including:
- GCC C compiler (gcc)
- GCC C++ compiler (g++)
- GNU archiver (ar)
- Linker (using gcc/g++)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pcons.configure.platform import get_platform
from pcons.core.builder import CommandBuilder, MultiOutputBuilder, OutputSpec
from pcons.core.subst import SourcePath, TargetPath
from pcons.toolchains.unix import UnixToolchain
from pcons.tools.tool import BaseTool

if TYPE_CHECKING:
    from pcons.core.builder import Builder
    from pcons.core.toolconfig import ToolConfig


class GccCCompiler(BaseTool):
    """GCC C compiler tool.

    Variables:
        cmd: Compiler command (default: 'gcc')
        flags: General compiler flags (list)
        iprefix: Include directory prefix (default: '-I')
        includes: Include directories (list of paths, no prefix)
        dprefix: Define prefix (default: '-D')
        defines: Preprocessor definitions (list of names, no prefix)
        depflags: Dependency generation flags
        objcmd: Command template for compiling to object
    """

    def __init__(self) -> None:
        super().__init__("cc", language="c")

    def default_vars(self) -> dict[str, object]:
        return {
            "cmd": "gcc",
            "flags": [],
            "iprefix": "-I",
            "includes": [],
            "dprefix": "-D",
            "defines": [],
            "depflags": ["-MD", "-MF", TargetPath(suffix=".d")],
            "objcmd": [
                "$cc.cmd",
                "$cc.flags",
                "${prefix(cc.iprefix, cc.includes)}",
                "${prefix(cc.dprefix, cc.defines)}",
                "$cc.depflags",
                "-c",
                "-o",
                TargetPath(),
                SourcePath(),
            ],
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
                depfile=TargetPath(suffix=".d"),
                deps_style="gcc",
            ),
        }

    def configure(self, config: object) -> ToolConfig | None:
        from pcons.configure.config import Configure

        if not isinstance(config, Configure):
            return None

        gcc = config.find_program("gcc")
        if gcc is None:
            gcc = config.find_program("cc")
        if gcc is None:
            return None

        from pcons.core.toolconfig import ToolConfig

        tool_config = ToolConfig("cc", cmd=str(gcc.path))
        if gcc.version:
            tool_config.version = gcc.version
        return tool_config


class GccCxxCompiler(BaseTool):
    """GCC C++ compiler tool.

    Variables:
        cmd: Compiler command (default: 'g++')
        flags: General compiler flags (list)
        iprefix: Include directory prefix (default: '-I')
        includes: Include directories (list of paths, no prefix)
        dprefix: Define prefix (default: '-D')
        defines: Preprocessor definitions (list of names, no prefix)
        depflags: Dependency generation flags
        objcmd: Command template for compiling to object
    """

    def __init__(self) -> None:
        super().__init__("cxx", language="cxx")

    def default_vars(self) -> dict[str, object]:
        return {
            "cmd": "g++",
            "flags": [],
            "iprefix": "-I",
            "includes": [],
            "dprefix": "-D",
            "defines": [],
            "depflags": ["-MD", "-MF", TargetPath(suffix=".d")],
            "objcmd": [
                "$cxx.cmd",
                "$cxx.flags",
                "${prefix(cxx.iprefix, cxx.includes)}",
                "${prefix(cxx.dprefix, cxx.defines)}",
                "$cxx.depflags",
                "-c",
                "-o",
                TargetPath(),
                SourcePath(),
            ],
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
                depfile=TargetPath(suffix=".d"),
                deps_style="gcc",
            ),
        }

    def configure(self, config: object) -> ToolConfig | None:
        from pcons.configure.config import Configure

        if not isinstance(config, Configure):
            return None

        gxx = config.find_program("g++")
        if gxx is None:
            gxx = config.find_program("c++")
        if gxx is None:
            return None

        from pcons.core.toolconfig import ToolConfig

        tool_config = ToolConfig("cxx", cmd=str(gxx.path))
        if gxx.version:
            tool_config.version = gxx.version
        return tool_config


class GccArchiver(BaseTool):
    """GNU archiver tool for creating static libraries.

    Variables:
        cmd: Archiver command (default: 'ar')
        flags: Archiver flags (default: 'rcs')
        libcmd: Command template for creating static library
    """

    def __init__(self) -> None:
        super().__init__("ar")

    def default_vars(self) -> dict[str, object]:
        return {
            "cmd": "ar",
            "flags": ["rcs"],
            "libcmd": ["$ar.cmd", "$ar.flags", TargetPath(), SourcePath()],
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
        from pcons.configure.config import Configure

        if not isinstance(config, Configure):
            return None

        ar = config.find_program("ar")
        if ar is None:
            return None

        from pcons.core.toolconfig import ToolConfig

        return ToolConfig("ar", cmd=str(ar.path))


class GccLinker(BaseTool):
    """GCC linker tool.

    Variables:
        cmd: Linker command (default: 'gcc')
        flags: Linker flags (list)
        lprefix: Library prefix (default: '-l')
        libs: Libraries to link (list of names, no prefix)
        Lprefix: Library directory prefix (default: '-L')
        libdirs: Library directories (list of paths, no prefix)
        Fprefix: Framework directory prefix (default: '-F', macOS only)
        frameworkdirs: Framework directories (list of paths, no prefix)
        fprefix: Framework prefix (default: '-framework', macOS only)
        frameworks: Frameworks to link (list of names, no prefix)
        progcmd: Command template for linking program
        sharedcmd: Command template for linking shared library
    """

    def __init__(self) -> None:
        super().__init__("link")

    def default_vars(self) -> dict[str, object]:
        platform = get_platform()
        shared_flag = "-dynamiclib" if platform.is_macos else "-shared"
        return {
            "cmd": "gcc",
            "flags": [],
            "lprefix": "-l",
            "libs": [],
            "Lprefix": "-L",
            "libdirs": [],
            # Framework support (macOS only, but always defined for portability)
            "Fprefix": "-F",
            "frameworkdirs": [],
            "fprefix": "-framework",
            "frameworks": [],
            "progcmd": [
                "$link.cmd",
                "$link.flags",
                "-o",
                TargetPath(),
                SourcePath(),
                "${prefix(link.Lprefix, link.libdirs)}",
                "${prefix(link.lprefix, link.libs)}",
                "${prefix(link.Fprefix, link.frameworkdirs)}",
                "${pairwise(link.fprefix, link.frameworks)}",
            ],
            "sharedcmd": [
                "$link.cmd",
                shared_flag,
                "$link.flags",
                "-o",
                TargetPath(),
                SourcePath(),
                "${prefix(link.Lprefix, link.libdirs)}",
                "${prefix(link.lprefix, link.libs)}",
                "${prefix(link.Fprefix, link.frameworkdirs)}",
                "${pairwise(link.fprefix, link.frameworks)}",
            ],
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
            "SharedLibrary": MultiOutputBuilder(
                "SharedLibrary",
                "link",
                "sharedcmd",
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

        gcc = config.find_program("gcc")
        if gcc is None:
            gcc = config.find_program("cc")
        if gcc is None:
            return None

        from pcons.core.toolconfig import ToolConfig

        return ToolConfig("link", cmd=str(gcc.path))


class GccToolchain(UnixToolchain):
    """GCC toolchain for C and C++ development.

    Includes: C compiler (gcc), C++ compiler (g++), archiver (ar), linker (gcc/g++)

    Inherits from UnixToolchain which provides:
    - get_source_handler() for C/C++/Objective-C/assembly files
    - get_object_suffix(), get_static_library_name(), etc.
    - get_compile_flags_for_target_type() for -fPIC handling
    - get_separated_arg_flags() for flags like -arch, -framework, etc.
    - apply_target_arch() for macOS cross-compilation
    - apply_variant() for debug/release/etc. configurations
    """

    def __init__(self) -> None:
        super().__init__("gcc")

    def _configure_tools(self, config: object) -> bool:
        from pcons.configure.config import Configure

        if not isinstance(config, Configure):
            return False

        cc = GccCCompiler()
        if cc.configure(config) is None:
            return False

        cxx = GccCxxCompiler()
        cxx.configure(config)

        ar = GccArchiver()
        ar.configure(config)

        link = GccLinker()
        if link.configure(config) is None:
            return False

        self._tools = {"cc": cc, "cxx": cxx, "ar": ar, "link": link}
        return True


# =============================================================================
# Registration
# =============================================================================

from pcons.tools.toolchain import toolchain_registry  # noqa: E402

toolchain_registry.register(
    GccToolchain,
    aliases=["gcc", "gnu"],
    check_command="gcc",
    tool_classes=[GccCCompiler, GccCxxCompiler, GccArchiver, GccLinker],
    category="c",
)
