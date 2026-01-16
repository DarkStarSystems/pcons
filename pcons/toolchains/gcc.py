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
from pcons.core.builder import CommandBuilder
from pcons.tools.tool import BaseTool
from pcons.tools.toolchain import BaseToolchain

if TYPE_CHECKING:
    from pcons.core.builder import Builder
    from pcons.core.environment import Environment
    from pcons.core.toolconfig import ToolConfig


class GccCCompiler(BaseTool):
    """GCC C compiler tool.

    Provides the 'cc' tool for compiling C source files to object files.

    Variables:
        cmd: Compiler command (default: 'gcc')
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
            "cmd": "gcc",
            "flags": [],
            "includes": [],
            "defines": [],
            "depflags": "-MD -MF $$out.d",
            "objcmd": "$cc.cmd $cc.flags $cc.includes $cc.defines $cc.depflags -c -o $$out $$in",
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
        """Detect GCC C compiler."""
        from pcons.configure.config import Configure
        if not isinstance(config, Configure):
            return None

        # Try to find gcc
        gcc = config.find_program("gcc")
        if gcc is None:
            # Try cc as fallback
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

    Provides the 'cxx' tool for compiling C++ source files to object files.

    Variables:
        cmd: Compiler command (default: 'g++')
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
            "cmd": "g++",
            "flags": [],
            "includes": [],
            "defines": [],
            "depflags": "-MD -MF $$out.d",
            "objcmd": "$cxx.cmd $cxx.flags $cxx.includes $cxx.defines $cxx.depflags -c -o $$out $$in",
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
        """Detect GCC C++ compiler."""
        from pcons.configure.config import Configure
        if not isinstance(config, Configure):
            return None

        # Try to find g++
        gxx = config.find_program("g++")
        if gxx is None:
            # Try c++ as fallback
            gxx = config.find_program("c++")

        if gxx is None:
            return None

        from pcons.core.toolconfig import ToolConfig
        tool_config = ToolConfig("cxx", cmd=str(gxx.path))
        if gxx.version:
            tool_config.version = gxx.version

        return tool_config


class GccArchiver(BaseTool):
    """GNU archiver tool.

    Provides the 'ar' tool for creating static libraries.

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
            "flags": "rcs",
            "libcmd": "$ar.cmd $ar.flags $$out $$in",
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
        """Detect GNU archiver."""
        from pcons.configure.config import Configure
        if not isinstance(config, Configure):
            return None

        ar = config.find_program("ar")
        if ar is None:
            return None

        from pcons.core.toolconfig import ToolConfig
        tool_config = ToolConfig("ar", cmd=str(ar.path))
        return tool_config


class GccLinker(BaseTool):
    """GCC linker tool.

    Provides the 'link' tool for linking object files into executables
    or shared libraries. Uses gcc/g++ as the linker driver.

    Variables:
        cmd: Linker command (default: 'gcc', may be changed to 'g++')
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
            "cmd": "gcc",
            "flags": [],
            "libs": [],
            "libdirs": [],
            "progcmd": "$link.cmd $link.flags -o $$out $$in $link.libdirs $link.libs",
            "sharedcmd": f"$link.cmd {shared_flag} $link.flags -o $$out $$in $link.libdirs $link.libs",
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

        # Use gcc as the linker driver
        gcc = config.find_program("gcc")
        if gcc is None:
            gcc = config.find_program("cc")

        if gcc is None:
            return None

        from pcons.core.toolconfig import ToolConfig
        tool_config = ToolConfig("link", cmd=str(gcc.path))
        return tool_config


class GccToolchain(BaseToolchain):
    """GCC toolchain.

    A complete GCC-based toolchain for C and C++ development.
    Includes:
    - C compiler (gcc)
    - C++ compiler (g++)
    - Archiver (ar)
    - Linker (gcc/g++)

    Example:
        config = Configure()
        gcc = GccToolchain()
        if gcc.configure(config):
            env = project.Environment(toolchain=gcc)
            env.cc.Object("main.o", "main.c")
    """

    def __init__(self) -> None:
        super().__init__("gcc")

    def _configure_tools(self, config: object) -> bool:
        """Configure all GCC tools."""
        from pcons.configure.config import Configure
        if not isinstance(config, Configure):
            return False

        # Try to configure each tool
        cc = GccCCompiler()
        cc_config = cc.configure(config)
        if cc_config is None:
            return False

        cxx = GccCxxCompiler()
        cxx.configure(config)  # C++ is optional

        ar = GccArchiver()
        ar.configure(config)  # Archiver is optional

        link = GccLinker()
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

    def setup(self, env: Environment) -> None:
        """Set up all tools in the environment."""
        super().setup(env)

        # Set up convenience builders at environment level
        # These delegate to the appropriate tool based on source suffix
        pass  # Tool setup handles this via BaseTool.setup()

    def apply_variant(self, env: Environment, variant: str, **kwargs: Any) -> None:
        """Apply a build variant to the environment.

        Implements GCC-specific handling for standard build variants.

        Args:
            env: Environment to configure.
            variant: Variant name. Recognized values:
                - "debug": No optimization, debug symbols, DEBUG defined
                - "release": Full optimization, NDEBUG defined
                - "relwithdebinfo": Optimization with debug symbols
                - "minsizerel": Size optimization
            **kwargs: Additional options:
                - extra_flags: Additional compiler flags
                - extra_defines: Additional preprocessor definitions
        """
        # Call base to set env.variant
        super().apply_variant(env, variant, **kwargs)

        extra_flags = kwargs.get("extra_flags", [])
        extra_defines = kwargs.get("extra_defines", [])

        # Collect flags based on variant
        compile_flags: list[str] = []
        defines: list[str] = []
        link_flags: list[str] = []

        variant_lower = variant.lower()
        if variant_lower == "debug":
            compile_flags = ["-O0", "-g"]
            defines = ["-DDEBUG", "-D_DEBUG"]
        elif variant_lower == "release":
            compile_flags = ["-O2"]
            defines = ["-DNDEBUG"]
        elif variant_lower == "relwithdebinfo":
            compile_flags = ["-O2", "-g"]
            defines = ["-DNDEBUG"]
        elif variant_lower == "minsizerel":
            compile_flags = ["-Os"]
            defines = ["-DNDEBUG"]
        # else: unknown variant, leave flags empty (user manages)

        # Add extra flags
        for flag in extra_flags:
            compile_flags.append(flag)
        for define in extra_defines:
            defines.append(f"-D{define}")

        # Apply to C compiler
        if env.has_tool("cc"):
            cc = env.cc
            current_flags = getattr(cc, "flags", [])
            if isinstance(current_flags, list):
                current_flags.extend(compile_flags)
            current_defines = getattr(cc, "defines", [])
            if isinstance(current_defines, list):
                current_defines.extend(defines)

        # Apply to C++ compiler
        if env.has_tool("cxx"):
            cxx = env.cxx
            current_flags = getattr(cxx, "flags", [])
            if isinstance(current_flags, list):
                current_flags.extend(compile_flags)
            current_defines = getattr(cxx, "defines", [])
            if isinstance(current_defines, list):
                current_defines.extend(defines)

        # Apply to linker
        if env.has_tool("link"):
            link = env.link
            current_flags = getattr(link, "flags", [])
            if isinstance(current_flags, list):
                current_flags.extend(link_flags)


# =============================================================================
# Registration
# =============================================================================

# Register GCC toolchain for auto-discovery
from pcons.tools.toolchain import toolchain_registry

toolchain_registry.register(
    GccToolchain,
    aliases=["gcc", "gnu"],
    check_command="gcc",
    tool_classes=[GccCCompiler, GccCxxCompiler, GccArchiver, GccLinker],
    category="c",
)
