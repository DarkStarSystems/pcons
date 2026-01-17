# SPDX-License-Identifier: MIT
"""MSVC toolchain implementation (Windows only)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
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


def _find_vswhere() -> Path | None:
    program_files = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    vswhere = Path(program_files) / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
    return vswhere if vswhere.exists() else None


def _find_msvc_install() -> Path | None:
    vswhere = _find_vswhere()
    if vswhere is None:
        return None
    try:
        result = subprocess.run(
            [str(vswhere), "-latest", "-requires",
             "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
             "-property", "installationPath"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            return Path(result.stdout.strip())
    except (subprocess.TimeoutExpired, OSError):
        pass
    return None


class MsvcCompiler(BaseTool):
    """MSVC C/C++ compiler tool."""

    def __init__(self, name: str = "cc", language: str = "c") -> None:
        super().__init__(name, language=language)

    def default_vars(self) -> dict[str, object]:
        return {
            "cmd": "cl.exe",
            "flags": ["/nologo"],
            "iprefix": "/I",
            "includes": [],
            "dprefix": "/D",
            "defines": [],
            "depflags": ["/showIncludes"],
            "objcmd": [
                "$cc.cmd",
                "$cc.flags",
                "${prefix(cc.iprefix, cc.includes)}",
                "${prefix(cc.dprefix, cc.defines)}",
                "$cc.depflags",
                "/c", "/Fo$$out", "$$in",
            ],
        }

    def builders(self) -> dict[str, Builder]:
        return {
            "Object": CommandBuilder(
                "Object", self._name, "objcmd",
                src_suffixes=[".c", ".cpp", ".cxx", ".cc"],
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

        cl = config.find_program("cl.exe", version_flag="")
        if cl is None:
            vs_path = _find_msvc_install()
            if vs_path:
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
        return ToolConfig(self._name, cmd=str(cl.path))


class MsvcLibrarian(BaseTool):
    """MSVC librarian tool."""

    def __init__(self) -> None:
        super().__init__("lib")

    def default_vars(self) -> dict[str, object]:
        return {
            "cmd": "lib.exe",
            "flags": ["/nologo"],
            "libcmd": ["$lib.cmd", "$lib.flags", "/OUT:$$out", "$$in"],
        }

    def builders(self) -> dict[str, Builder]:
        return {
            "StaticLibrary": CommandBuilder(
                "StaticLibrary", "lib", "libcmd",
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
        lib = config.find_program("lib.exe", version_flag="")
        if lib is None:
            return None
        from pcons.core.toolconfig import ToolConfig
        return ToolConfig("lib", cmd=str(lib.path))


class MsvcLinker(BaseTool):
    """MSVC linker tool."""

    def __init__(self) -> None:
        super().__init__("link")

    def default_vars(self) -> dict[str, object]:
        return {
            "cmd": "link.exe",
            "flags": ["/nologo"],
            "libs": [],
            "Lprefix": "/LIBPATH:",
            "libdirs": [],
            "progcmd": [
                "$link.cmd", "$link.flags",
                "/OUT:$$out", "$$in",
                "${prefix(link.Lprefix, link.libdirs)}",
                "$link.libs",
            ],
            "sharedcmd": [
                "$link.cmd", "/DLL", "$link.flags",
                "/OUT:$$out", "/IMPLIB:$$out_import_lib",
                "$$in",
                "${prefix(link.Lprefix, link.libdirs)}",
                "$link.libs",
            ],
        }

    def builders(self) -> dict[str, Builder]:
        return {
            "Program": CommandBuilder(
                "Program", "link", "progcmd",
                src_suffixes=[".obj"],
                target_suffixes=[".exe"],
                single_source=False,
            ),
            "SharedLibrary": MultiOutputBuilder(
                "SharedLibrary", "link", "sharedcmd",
                outputs=[
                    OutputSpec("primary", ".dll"),
                    OutputSpec("import_lib", ".lib"),
                    OutputSpec("export_file", ".exp", implicit=True),
                ],
                src_suffixes=[".obj"],
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
        link = config.find_program("link.exe", version_flag="")
        if link is None:
            return None
        from pcons.core.toolconfig import ToolConfig
        return ToolConfig("link", cmd=str(link.path))


class MsvcToolchain(BaseToolchain):
    """Microsoft Visual C++ toolchain (Windows only)."""

    def __init__(self) -> None:
        super().__init__("msvc")

    # =========================================================================
    # Source Handler Methods
    # =========================================================================

    def get_source_handler(self, suffix: str) -> "SourceHandler | None":
        """Return handler for source file suffix, or None if not handled."""
        from pcons.tools.toolchain import SourceHandler

        suffix_lower = suffix.lower()
        if suffix_lower == ".c":
            return SourceHandler("cc", "c", ".obj", None, "msvc")
        if suffix_lower in (".cpp", ".cxx", ".cc"):
            return SourceHandler("cxx", "cxx", ".obj", None, "msvc")
        return None

    def get_object_suffix(self) -> str:
        """Return the object file suffix for MSVC toolchain."""
        return ".obj"

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
        """Return additional compile flags needed for the target type.

        MSVC does not need special compile flags for shared libraries.
        DLL exports are typically handled via __declspec(dllexport) in code
        or via module definition files (.def), not compiler flags.

        Args:
            target_type: The target type (e.g., "shared_library", "static_library").

        Returns:
            List of additional compile flags (empty for MSVC).
        """
        # MSVC doesn't need special compile flags like -fPIC.
        # DLL export/import is handled via:
        # - __declspec(dllexport) / __declspec(dllimport) in source code
        # - Module definition files (.def)
        # - /EXPORT linker flag (not a compile flag)
        return []

    def _configure_tools(self, config: object) -> bool:
        from pcons.configure.config import Configure
        if not isinstance(config, Configure):
            return False
        platform = get_platform()
        if not platform.is_windows:
            return False

        cc = MsvcCompiler("cc", "c")
        if cc.configure(config) is None:
            return False

        cxx = MsvcCompiler("cxx", "cxx")
        cxx.configure(config)

        lib = MsvcLibrarian()
        lib.configure(config)

        link = MsvcLinker()
        if link.configure(config) is None:
            return False

        self._tools = {"cc": cc, "cxx": cxx, "lib": lib, "link": link}
        return True

    def apply_variant(self, env: Environment, variant: str, **kwargs: Any) -> None:
        """Apply build variant with MSVC flags."""
        super().apply_variant(env, variant, **kwargs)

        compile_flags: list[str] = []
        defines: list[str] = []
        link_flags: list[str] = []

        variant_lower = variant.lower()
        if variant_lower == "debug":
            compile_flags = ["/Od", "/Zi"]
            defines = ["DEBUG", "_DEBUG"]
            link_flags = ["/DEBUG"]
        elif variant_lower == "release":
            compile_flags = ["/O2"]
            defines = ["NDEBUG"]
        elif variant_lower == "relwithdebinfo":
            compile_flags = ["/O2", "/Zi"]
            defines = ["NDEBUG"]
            link_flags = ["/DEBUG"]
        elif variant_lower == "minsizerel":
            compile_flags = ["/O1"]
            defines = ["NDEBUG"]

        for tool_name in ("cc", "cxx"):
            if env.has_tool(tool_name):
                tool = getattr(env, tool_name)
                if hasattr(tool, "flags") and isinstance(tool.flags, list):
                    tool.flags.extend(compile_flags)
                if hasattr(tool, "defines") and isinstance(tool.defines, list):
                    tool.defines.extend(defines)

        if env.has_tool("link") and link_flags:
            if isinstance(env.link.flags, list):
                env.link.flags.extend(link_flags)


# =============================================================================
# Registration
# =============================================================================

from pcons.tools.toolchain import toolchain_registry

toolchain_registry.register(
    MsvcToolchain,
    aliases=["msvc", "vc", "visualstudio"],
    check_command="cl.exe",
    tool_classes=[MsvcCompiler, MsvcLibrarian, MsvcLinker],
    category="c",
)
