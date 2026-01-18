# SPDX-License-Identifier: MIT
"""GCC toolchain implementation.

Provides GCC-based C and C++ compilation toolchain including:
- GCC C compiler (gcc)
- GCC C++ compiler (g++)
- GNU archiver (ar)
- Linker (using gcc/g++)
"""

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
            "depflags": ["-MD", "-MF", "$$out.d"],
            "objcmd": [
                "$cc.cmd",
                "$cc.flags",
                "${prefix(cc.iprefix, cc.includes)}",
                "${prefix(cc.dprefix, cc.defines)}",
                "$cc.depflags",
                "-c",
                "-o",
                "$$out",
                "$$in",
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
                depfile="$out.d",
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
            "depflags": ["-MD", "-MF", "$$out.d"],
            "objcmd": [
                "$cxx.cmd",
                "$cxx.flags",
                "${prefix(cxx.iprefix, cxx.includes)}",
                "${prefix(cxx.dprefix, cxx.defines)}",
                "$cxx.depflags",
                "-c",
                "-o",
                "$$out",
                "$$in",
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
                depfile="$out.d",
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
            "libcmd": ["$ar.cmd", "$ar.flags", "$$out", "$$in"],
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
            "progcmd": [
                "$link.cmd",
                "$link.flags",
                "-o",
                "$$out",
                "$$in",
                "${prefix(link.Lprefix, link.libdirs)}",
                "${prefix(link.lprefix, link.libs)}",
            ],
            "sharedcmd": [
                "$link.cmd",
                shared_flag,
                "$link.flags",
                "-o",
                "$$out",
                "$$in",
                "${prefix(link.Lprefix, link.libdirs)}",
                "${prefix(link.lprefix, link.libs)}",
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


class GccToolchain(BaseToolchain):
    """GCC toolchain for C and C++ development.

    Includes: C compiler (gcc), C++ compiler (g++), archiver (ar), linker (gcc/g++)
    """

    def __init__(self) -> None:
        super().__init__("gcc")

    # =========================================================================
    # Source Handler Methods
    # =========================================================================

    def get_source_handler(self, suffix: str) -> SourceHandler | None:
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
        # Assembly files - GCC/GAS handles .s (preprocessed) and .S (needs preprocessing)
        # Both are processed by the C compiler which invokes the assembler
        # Check .S (uppercase) first since .S.lower() == ".s"
        if suffix == ".S":
            # .S files need C preprocessing, so they can have dependencies
            return SourceHandler("cc", "asm-cpp", ".o", "$out.d", "gcc")
        if suffix_lower == ".s":
            # .s files are already preprocessed assembly, no dependency tracking
            return SourceHandler("cc", "asm", ".o", None, None)
        return None

    def get_object_suffix(self) -> str:
        """Return the object file suffix for GCC toolchain."""
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

    def get_compile_flags_for_target_type(self, target_type: str) -> list[str]:
        """Return additional compile flags needed for the target type.

        For GCC on Linux, shared libraries need -fPIC.
        On macOS, PIC is the default for 64-bit, so no flag is needed.

        Args:
            target_type: The target type (e.g., "shared_library", "static_library").

        Returns:
            List of additional compile flags.
        """
        platform = get_platform()

        if target_type == "shared_library":
            # On Linux (and other non-macOS POSIX systems), we need -fPIC
            # for position-independent code in shared libraries.
            # On macOS 64-bit, PIC is the default, so no flag needed.
            if platform.is_linux or (platform.is_posix and not platform.is_macos):
                return ["-fPIC"]

        # Static libraries, programs, and other types don't need special flags
        return []

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

    def apply_variant(self, env: Environment, variant: str, **kwargs: Any) -> None:
        """Apply build variant (debug, release, etc.).

        Args:
            env: Environment to modify.
            variant: Variant name (debug, release, relwithdebinfo, minsizerel).
            **kwargs: Optional extra_flags and extra_defines to add.
        """
        super().apply_variant(env, variant, **kwargs)

        compile_flags: list[str] = []
        defines: list[str] = []
        link_flags: list[str] = []

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

        # Add extra flags/defines from kwargs
        extra_flags = kwargs.get("extra_flags", [])
        extra_defines = kwargs.get("extra_defines", [])
        compile_flags.extend(extra_flags)
        defines.extend(extra_defines)

        # Apply to compilers
        for tool_name in ("cc", "cxx"):
            if env.has_tool(tool_name):
                tool = getattr(env, tool_name)
                if hasattr(tool, "flags") and isinstance(tool.flags, list):
                    tool.flags.extend(compile_flags)
                if hasattr(tool, "defines") and isinstance(tool.defines, list):
                    tool.defines.extend(defines)

        # Apply to linker
        if env.has_tool("link") and link_flags:
            if isinstance(env.link.flags, list):
                env.link.flags.extend(link_flags)


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
