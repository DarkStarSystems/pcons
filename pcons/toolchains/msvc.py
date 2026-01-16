# SPDX-License-Identifier: MIT
"""MSVC toolchain implementation.

Provides Microsoft Visual C++ compilation toolchain including:
- MSVC C/C++ compiler (cl.exe)
- MSVC librarian (lib.exe)
- MSVC linker (link.exe)

Note: This toolchain is only available on Windows.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from pcons.configure.platform import get_platform
from pcons.core.builder import CommandBuilder
from pcons.tools.tool import BaseTool
from pcons.tools.toolchain import BaseToolchain

if TYPE_CHECKING:
    from pcons.core.builder import Builder
    from pcons.core.toolconfig import ToolConfig


def _find_vswhere() -> Path | None:
    """Find vswhere.exe to locate Visual Studio installations."""
    # vswhere is typically installed here
    program_files = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    vswhere = Path(program_files) / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
    if vswhere.exists():
        return vswhere
    return None


def _find_msvc_install() -> Path | None:
    """Find the MSVC installation directory."""
    vswhere = _find_vswhere()
    if vswhere is None:
        return None

    try:
        result = subprocess.run(
            [
                str(vswhere),
                "-latest",
                "-requires", "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
                "-property", "installationPath",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            install_path = result.stdout.strip()
            if install_path:
                return Path(install_path)
    except (subprocess.TimeoutExpired, OSError):
        pass

    return None


class MsvcCompiler(BaseTool):
    """MSVC C/C++ compiler tool.

    Provides the 'cl' tool for compiling C and C++ source files.
    Note: MSVC uses the same compiler for both C and C++.

    Variables:
        cmd: Compiler command (default: 'cl.exe')
        flags: Compiler flags
        includes: Include directories (/I flags)
        defines: Preprocessor definitions (/D flags)
        objcmd: Command template for compiling to object
    """

    def __init__(self, name: str = "cc", language: str = "c") -> None:
        super().__init__(name, language=language)

    def default_vars(self) -> dict[str, object]:
        return {
            "cmd": "cl.exe",
            "flags": ["/nologo"],
            "includes": [],
            "defines": [],
            "objcmd": "$cc.cmd $cc.flags $cc.includes $cc.defines /c /Fo$out $in",
        }

    def builders(self) -> dict[str, Builder]:
        return {
            "Object": CommandBuilder(
                "Object",
                self._name,
                "objcmd",
                src_suffixes=[".c", ".cpp", ".cxx", ".cc"],
                target_suffixes=[".obj"],
                language=self._language,
                single_source=True,
            ),
        }

    def configure(self, config: object) -> ToolConfig | None:
        """Detect MSVC compiler."""
        from pcons.configure.config import Configure
        if not isinstance(config, Configure):
            return None

        platform = get_platform()
        if not platform.is_windows:
            return None

        # Try to find cl.exe
        cl = config.find_program("cl.exe", version_flag="")
        if cl is None:
            # Try to find via Visual Studio installation
            vs_path = _find_msvc_install()
            if vs_path:
                # Look for cl.exe in the VC tools directory
                # This is a simplified search - real implementation would
                # need to handle different VS versions and architectures
                vc_tools = vs_path / "VC" / "Tools" / "MSVC"
                if vc_tools.exists():
                    for version_dir in sorted(vc_tools.iterdir(), reverse=True):
                        cl_path = version_dir / "bin" / "Hostx64" / "x64" / "cl.exe"
                        if cl_path.exists():
                            from pcons.configure.config import ProgramInfo
                            cl = ProgramInfo(path=cl_path)
                            break

        if cl is None:
            return None

        from pcons.core.toolconfig import ToolConfig
        tool_config = ToolConfig(self._name, cmd=str(cl.path))
        return tool_config


class MsvcLibrarian(BaseTool):
    """MSVC librarian tool.

    Provides the 'lib' tool for creating static libraries.

    Variables:
        cmd: Librarian command (default: 'lib.exe')
        flags: Librarian flags
        libcmd: Command template for creating static library
    """

    def __init__(self) -> None:
        super().__init__("lib")

    def default_vars(self) -> dict[str, object]:
        return {
            "cmd": "lib.exe",
            "flags": ["/nologo"],
            "libcmd": "$lib.cmd $lib.flags /OUT:$out $in",
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
        """Detect MSVC librarian."""
        from pcons.configure.config import Configure
        if not isinstance(config, Configure):
            return None

        platform = get_platform()
        if not platform.is_windows:
            return None

        lib = config.find_program("lib.exe", version_flag="")
        if lib is None:
            return None

        from pcons.core.toolconfig import ToolConfig
        tool_config = ToolConfig("lib", cmd=str(lib.path))
        return tool_config


class MsvcLinker(BaseTool):
    """MSVC linker tool.

    Provides the 'link' tool for linking object files into executables
    or DLLs.

    Variables:
        cmd: Linker command (default: 'link.exe')
        flags: Linker flags
        libs: Libraries to link
        libdirs: Library directories (/LIBPATH flags)
        progcmd: Command template for linking program
        sharedcmd: Command template for linking DLL
    """

    def __init__(self) -> None:
        super().__init__("link")

    def default_vars(self) -> dict[str, object]:
        return {
            "cmd": "link.exe",
            "flags": ["/nologo"],
            "libs": [],
            "libdirs": [],
            "progcmd": "$link.cmd $link.flags /OUT:$out $in $link.libdirs $link.libs",
            "sharedcmd": "$link.cmd /DLL $link.flags /OUT:$out $in $link.libdirs $link.libs",
        }

    def builders(self) -> dict[str, Builder]:
        return {
            "Program": CommandBuilder(
                "Program",
                "link",
                "progcmd",
                src_suffixes=[".obj"],
                target_suffixes=[".exe"],
                single_source=False,
            ),
            "SharedLibrary": CommandBuilder(
                "SharedLibrary",
                "link",
                "sharedcmd",
                src_suffixes=[".obj"],
                target_suffixes=[".dll"],
                single_source=False,
            ),
        }

    def configure(self, config: object) -> ToolConfig | None:
        """Detect MSVC linker."""
        from pcons.configure.config import Configure
        if not isinstance(config, Configure):
            return None

        platform = get_platform()
        if not platform.is_windows:
            return None

        link = config.find_program("link.exe", version_flag="")
        if link is None:
            return None

        from pcons.core.toolconfig import ToolConfig
        tool_config = ToolConfig("link", cmd=str(link.path))
        return tool_config


class MsvcToolchain(BaseToolchain):
    """Microsoft Visual C++ toolchain.

    A complete MSVC-based toolchain for C and C++ development on Windows.
    Includes:
    - C/C++ compiler (cl.exe)
    - Librarian (lib.exe)
    - Linker (link.exe)

    Note: This toolchain is only available on Windows.

    Example:
        config = Configure()
        msvc = MsvcToolchain()
        if msvc.configure(config):
            env = project.Environment(toolchain=msvc)
            env.cc.Object("main.obj", "main.c")
    """

    def __init__(self) -> None:
        super().__init__("msvc")

    def _configure_tools(self, config: object) -> bool:
        """Configure all MSVC tools."""
        from pcons.configure.config import Configure
        if not isinstance(config, Configure):
            return False

        platform = get_platform()
        if not platform.is_windows:
            return False

        # Try to configure each tool
        cc = MsvcCompiler("cc", "c")
        cc_config = cc.configure(config)
        if cc_config is None:
            return False

        cxx = MsvcCompiler("cxx", "cxx")
        cxx.configure(config)  # C++ is optional (uses same compiler)

        lib = MsvcLibrarian()
        lib.configure(config)  # Librarian is optional

        link = MsvcLinker()
        link_config = link.configure(config)
        if link_config is None:
            return False

        # Store configured tools
        self._tools = {
            "cc": cc,
            "cxx": cxx,
            "lib": lib,
            "link": link,
        }

        return True
