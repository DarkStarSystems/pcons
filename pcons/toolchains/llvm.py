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
        import shutil

        # Prefer llvm-ar if available, otherwise fall back to ar
        ar_cmd = "llvm-ar" if shutil.which("llvm-ar") else "ar"
        return {
            "cmd": ar_cmd,
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
        ar = config.find_program("llvm-ar")
        if ar is None:
            ar = config.find_program("ar")
        if ar is None:
            return None
        from pcons.core.toolconfig import ToolConfig

        return ToolConfig("ar", cmd=str(ar.path))


class LlvmLinker(BaseTool):
    """LLVM linker tool.

    Variables:
        cmd: Linker command (default: 'clang')
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
            "cmd": "clang",
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
                "$$out",
                "$$in",
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
                "$$out",
                "$$in",
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
        clang = config.find_program("clang")
        if clang is None:
            return None
        from pcons.core.toolconfig import ToolConfig

        return ToolConfig("link", cmd=str(clang.path))


class MetalCompiler(BaseTool):
    """Apple Metal shader compiler tool (macOS only).

    Compiles .metal shader files to .air (Apple Intermediate Representation).
    The resulting .air files can be linked with metallib to create .metallib archives.

    Variables:
        cmd: Compiler command (default: 'xcrun metal')
        flags: Compiler flags (list)
        iprefix: Include directory prefix (default: '-I')
        includes: Include directories (list of paths, no prefix)
        metalcmd: Command template for compiling to .air
    """

    def __init__(self) -> None:
        super().__init__("metal", language="metal")

    def default_vars(self) -> dict[str, object]:
        return {
            "cmd": "xcrun",
            "flags": [],
            "iprefix": "-I",
            "includes": [],
            "metalcmd": [
                "$metal.cmd",
                "metal",
                "$metal.flags",
                "${prefix(metal.iprefix, metal.includes)}",
                "-c",
                "-o",
                "$$out",
                "$$in",
            ],
        }

    def builders(self) -> dict[str, Builder]:
        return {
            "MetalObject": CommandBuilder(
                "MetalObject",
                "metal",
                "metalcmd",
                src_suffixes=[".metal"],
                target_suffixes=[".air"],
                language="metal",
                single_source=True,
            ),
        }

    def configure(self, config: object) -> ToolConfig | None:
        from pcons.configure.config import Configure

        if not isinstance(config, Configure):
            return None

        platform = get_platform()
        if not platform.is_macos:
            return None

        # Check if xcrun metal is available
        xcrun = config.find_program("xcrun", version_flag="")
        if xcrun is None:
            return None

        from pcons.core.toolconfig import ToolConfig

        return ToolConfig("metal", cmd=str(xcrun.path))


class LlvmToolchain(BaseToolchain):
    """LLVM/Clang toolchain for C and C++ development."""

    # Flags that take their argument as a separate token (e.g., "-F path" not "-Fpath")
    # LLVM/Clang is largely GCC-compatible, so it uses the same separated arg flags.
    SEPARATED_ARG_FLAGS: frozenset[str] = frozenset(
        [
            # Framework/library paths (macOS)
            "-F",
            "-framework",
            # Xcode/Apple toolchain
            "-iframework",
            # Linker flags that take arguments
            "-Wl,-rpath",
            "-Wl,-install_name",
            "-Wl,-soname",
            # Output-related
            "-o",
            "-MF",
            "-MT",
            "-MQ",
            # Linker script
            "-T",
            # Architecture
            "-arch",
            "-target",
            "--target",
            # Include/library search modifiers
            "-isystem",
            "-isysroot",
            "-iquote",
            "-idirafter",
            # Xlinker passthrough
            "-Xlinker",
            "-Xpreprocessor",
            "-Xassembler",
        ]
    )

    def __init__(self) -> None:
        super().__init__("llvm")

    # =========================================================================
    # Source Handler Methods
    # =========================================================================

    def get_source_handler(self, suffix: str) -> SourceHandler | None:
        """Return handler for source file suffix, or None if not handled."""
        from pcons.tools.toolchain import SourceHandler

        platform = get_platform()

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
        # Assembly files - Clang handles .s (preprocessed) and .S (needs preprocessing)
        # Both are processed by the C compiler which invokes the assembler
        # Check .S (uppercase) first since .S.lower() == ".s"
        if suffix == ".S":
            # .S files need C preprocessing, so they can have dependencies
            return SourceHandler("cc", "asm-cpp", ".o", "$out.d", "gcc")
        if suffix_lower == ".s":
            # .s files are already preprocessed assembly, no dependency tracking
            return SourceHandler("cc", "asm", ".o", None, None)
        # Metal shaders (macOS only)
        if suffix_lower == ".metal" and platform.is_macos:
            # Metal shaders compile to .air (Apple Intermediate Representation)
            # Uses the 'metal' tool with 'metalcmd' command variable
            return SourceHandler("metal", "metal", ".air", None, None, "metalcmd")
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

    def get_compile_flags_for_target_type(self, target_type: str) -> list[str]:
        """Return additional compile flags needed for the target type.

        For LLVM/Clang on Linux, shared libraries need -fPIC.
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

    def get_separated_arg_flags(self) -> frozenset[str]:
        """Return flags that take their argument as a separate token.

        Returns:
            A frozenset of LLVM/Clang flags that take separate arguments.
        """
        return self.SEPARATED_ARG_FLAGS

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

        # Add Metal compiler on macOS (optional - not required for toolchain to work)
        platform = get_platform()
        if platform.is_macos:
            metal = MetalCompiler()
            if metal.configure(config) is not None:
                self._tools["metal"] = metal

        return True

    def apply_target_arch(self, env: Environment, arch: str, **kwargs: Any) -> None:
        """Apply target architecture flags.

        On macOS, uses the -arch flag for cross-compilation (e.g., building
        arm64 binaries on x86_64 or vice versa). This enables building
        universal binaries by compiling each architecture separately and
        combining with lipo.

        On Linux and other platforms, Clang can use the --target flag for
        cross-compilation, but that typically requires a cross-compilation
        sysroot to be configured as well.

        Args:
            env: Environment to modify.
            arch: Architecture name (e.g., "arm64", "x86_64").
            **kwargs: Toolchain-specific options (unused).
        """
        super().apply_target_arch(env, arch, **kwargs)
        platform = get_platform()

        if platform.is_macos:
            # macOS uses -arch flag for universal binary builds
            arch_flags = ["-arch", arch]
            for tool_name in ("cc", "cxx"):
                if env.has_tool(tool_name):
                    tool = getattr(env, tool_name)
                    if hasattr(tool, "flags") and isinstance(tool.flags, list):
                        tool.flags.extend(arch_flags)
            if env.has_tool("link"):
                if isinstance(env.link.flags, list):
                    env.link.flags.extend(arch_flags)

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

from pcons.tools.toolchain import toolchain_registry  # noqa: E402

toolchain_registry.register(
    LlvmToolchain,
    aliases=["llvm", "clang"],
    check_command="clang",
    tool_classes=[
        ClangCCompiler,
        ClangCxxCompiler,
        LlvmArchiver,
        LlvmLinker,
        MetalCompiler,
    ],
    category="c",
)
