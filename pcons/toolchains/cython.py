# SPDX-License-Identifier: MIT
"""Cython toolchain implementation.

Provides Cython compilation support for creating Python extension modules
from .pyx files. The workflow is:
  1. .pyx → Cython → .c
  2. .c → C compiler → .o
  3. .o → Linker → .so/.pyd (Python extension)
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, cast

from pcons.configure.platform import get_platform
from pcons.core.builder import CommandBuilder
from pcons.core.subst import SourcePath, TargetPath
from pcons.tools.tool import BaseTool
from pcons.tools.toolchain import BaseToolchain

if TYPE_CHECKING:
    from pcons.core.builder import Builder
    from pcons.core.toolconfig import ToolConfig


def get_python_info() -> dict[str, str]:
    """Get Python configuration for building extensions.

    Returns:
        Dict with keys: include_dir, ext_suffix, lib_dir, libs
    """
    import sysconfig

    info: dict[str, str] = {}

    info["include_dir"] = sysconfig.get_path("include") or ""
    # e.g. ".cpython-312-darwin.so"
    info["ext_suffix"] = sysconfig.get_config_var("EXT_SUFFIX") or ".so"
    info["lib_dir"] = sysconfig.get_config_var("LIBDIR") or ""

    platform = get_platform()
    if platform.is_windows:
        # Windows extensions must link against pythonXY.lib; on Unix the
        # importing interpreter already provides the symbols.
        info["libs"] = f"python{sys.version_info.major}{sys.version_info.minor}"
    else:
        info["libs"] = ""

    return info


class CythonTranspiler(BaseTool):
    """Cython transpiler tool.

    Transpiles .pyx files to C source files.

    Variables:
        cmd: Cython command (default: 'cython')
        flags: Cython flags
        pyx_to_c_cmd: Command template for .pyx → .c
    """

    def __init__(self) -> None:
        super().__init__("cython", language="cython")

    def default_vars(self) -> dict[str, object]:
        return {
            "cmd": "cython",
            "flags": [],
            "pyx_to_c_cmd": [
                "$cython.cmd",
                "$cython.flags",
                "-o",
                TargetPath(),
                SourcePath(),
            ],
        }

    def builders(self) -> dict[str, Builder]:
        return {
            "Transpile": CommandBuilder(
                "Transpile",
                "cython",
                "pyx_to_c_cmd",
                src_suffixes=[".pyx"],
                target_suffixes=[".c"],
                language="cython",
                single_source=True,
            ),
        }

    def configure(self, config: object) -> ToolConfig | None:
        """Detect Cython."""
        from pcons.configure.config import Configure

        if not isinstance(config, Configure):
            return None

        cython = config.find_program("cython")
        if cython is None:
            cython = config.find_program("cython3")

        if cython is None:
            return None

        from pcons.core.toolconfig import ToolConfig

        tool_config = ToolConfig("cython", cmd=str(cython.path))
        if cython.version:
            tool_config.version = cython.version

        return tool_config


class _PythonInfoTool(BaseTool):
    """BaseTool that lazily caches interpreter info from get_python_info()."""

    _python_info: dict[str, str] | None = None

    def _get_python_info(self) -> dict[str, str]:
        if self._python_info is None:
            self._python_info = get_python_info()
        return self._python_info


class CythonCCompiler(_PythonInfoTool):
    """C compiler configured for Cython extension modules.

    This is a C compiler with Python-specific settings for building
    extension modules.

    Variables:
        cmd: Compiler command
        flags: Compiler flags (includes -fPIC for shared libs)
        includes: Include directories (includes Python headers)
        defines: Preprocessor definitions
        depflags: Dependency generation flags
        objcmd: Command template for compiling to object
    """

    def __init__(self) -> None:
        super().__init__("cycc", language="c")

    def default_vars(self) -> dict[str, object]:
        platform = get_platform()
        python_info = self._get_python_info()

        cmd = "clang" if platform.is_macos else "gcc"

        includes = []
        if python_info["include_dir"]:
            includes.append(python_info["include_dir"])

        flags = ["-fPIC"]
        if platform.is_macos:
            # Suppress common warnings from Cython-generated code
            flags.append("-Wno-unreachable-code")

        return {
            "cmd": cmd,
            "flags": flags,
            "iprefix": "-I",
            "includes": includes,
            "dprefix": "-D",
            "defines": [],
            "depflags": ["-MD", "-MF", TargetPath(suffix=".d")],
            "objcmd": [
                "$cycc.cmd",
                "$cycc.flags",
                "${prefix(cycc.iprefix, cycc.includes)}",
                "${prefix(cycc.dprefix, cycc.defines)}",
                "$cycc.depflags",
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
                "cycc",
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
        """Detect C compiler for Cython."""
        from pcons.configure.config import Configure

        if not isinstance(config, Configure):
            return None

        platform = get_platform()

        if platform.is_macos:
            compiler = config.find_program("clang")
        else:
            compiler = config.find_program("gcc")
            if compiler is None:
                compiler = config.find_program("clang")

        if compiler is None:
            return None

        from pcons.core.toolconfig import ToolConfig

        tool_config = ToolConfig("cycc", cmd=str(compiler.path))
        if compiler.version:
            tool_config.version = compiler.version

        return tool_config


class CythonLinker(_PythonInfoTool):
    """Linker for Cython extension modules.

    Creates shared libraries (.so/.pyd) that Python can import.

    Variables:
        cmd: Linker command
        flags: Linker flags (includes -shared)
        libs: Libraries to link
        libdirs: Library directories
        ext_suffix: Python extension suffix
        extcmd: Command template for linking extension
    """

    def __init__(self) -> None:
        super().__init__("cylink")

    def default_vars(self) -> dict[str, object]:
        platform = get_platform()
        python_info = self._get_python_info()

        cmd = "clang" if platform.is_macos else "gcc"

        if platform.is_macos:
            # macOS uses -bundle for Python extensions, not -shared
            flags = ["-bundle", "-undefined", "dynamic_lookup"]
        elif platform.is_windows:
            flags = ["-shared"]
        else:
            flags = ["-shared"]

        libdirs = []
        libs = []
        if python_info["lib_dir"]:
            libdirs.append(python_info["lib_dir"])
        if python_info["libs"]:
            libs.append(python_info["libs"])

        return {
            "cmd": cmd,
            "flags": flags,
            "lprefix": "-l",
            "libs": libs,
            "Lprefix": "-L",
            "libdirs": libdirs,
            "ext_suffix": python_info["ext_suffix"],
            "extcmd": [
                "$cylink.cmd",
                "$cylink.flags",
                "-o",
                TargetPath(),
                SourcePath(),
                "${prefix(cylink.Lprefix, cylink.libdirs)}",
                "${prefix(cylink.lprefix, cylink.libs)}",
            ],
        }

    def builders(self) -> dict[str, Builder]:
        python_info = self._get_python_info()
        ext_suffix = python_info["ext_suffix"]

        return {
            "Extension": CommandBuilder(
                "Extension",
                "cylink",
                "extcmd",
                src_suffixes=[get_platform().object_suffix],
                target_suffixes=[ext_suffix],
                single_source=False,
            ),
        }

    def configure(self, config: object) -> ToolConfig | None:
        """Detect linker for Cython."""
        from pcons.configure.config import Configure

        if not isinstance(config, Configure):
            return None

        platform = get_platform()

        if platform.is_macos:
            linker = config.find_program("clang")
        else:
            linker = config.find_program("gcc")
            if linker is None:
                linker = config.find_program("clang")

        if linker is None:
            return None

        from pcons.core.toolconfig import ToolConfig

        tool_config = ToolConfig("cylink", cmd=str(linker.path))
        return tool_config


class CythonToolchain(BaseToolchain):
    """Cython toolchain for building Python extension modules
    (.pyx → .c → .o → .so/.pyd).

    Example:
        config = Configure()
        cython = CythonToolchain()
        if cython.configure(config):
            env = project.Environment(toolchain=cython)

            # Build an extension module
            c_file = env.cython.Transpile("hello.c", "hello.pyx")
            obj = env.cycc.Object("hello.o", c_file)
            env.cylink.Extension("hello", obj)
    """

    TOOL_NAMES = ("cython", "cycc", "cylink")

    def __init__(self) -> None:
        super().__init__("cython")

    def _configure_tools(self, config: object) -> bool:
        """Configure all Cython tools."""
        from pcons.configure.config import Configure

        if not isinstance(config, Configure):
            return False

        cython = CythonTranspiler()
        cython_config = cython.configure(config)
        if cython_config is None:
            return False

        cycc = CythonCCompiler()
        cycc_config = cycc.configure(config)
        if cycc_config is None:
            return False

        cylink = CythonLinker()
        cylink_config = cylink.configure(config)
        if cylink_config is None:
            return False

        self._tools = {
            "cython": cython,
            "cycc": cycc,
            "cylink": cylink,
        }

        return True


# =============================================================================
# Finder
# =============================================================================


def find_cython_toolchain() -> CythonToolchain:
    """Find and return a configured Cython toolchain.

    Returns:
        A configured CythonToolchain ready for use.

    Raises:
        RuntimeError: If Cython is not installed.
    """
    from pcons.tools.toolchain import toolchain_registry

    toolchain = toolchain_registry.find_available("python", ["cython"])
    if toolchain is not None:
        return cast(CythonToolchain, toolchain)

    raise RuntimeError(
        "Cython not found. Install it with: pip install cython (or uv add cython)"
    )


# =============================================================================
# Registration
# =============================================================================

from pcons.tools.toolchain import toolchain_registry  # noqa: E402

toolchain_registry.register(
    CythonToolchain,
    aliases=["cython"],
    check_command="cython",
    tool_classes=[CythonTranspiler, CythonCCompiler, CythonLinker],
    category="python",
    platforms=["linux", "darwin", "win32"],
    description="Cython transpiler (.pyx to Python extension)",
    finder="find_cython_toolchain()",
)


toolchain_registry.register_finder(
    ["cython"],
    find_cython_toolchain,
    description="Auto-detect the Cython toolchain",
)
