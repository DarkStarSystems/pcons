# SPDX-License-Identifier: MIT
"""MSVC toolchain implementation (Windows only)."""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pcons.configure.platform import get_platform
from pcons.core.builder import CommandBuilder, MultiOutputBuilder, OutputSpec
from pcons.core.subst import SourcePath, TargetPath
from pcons.tools.tool import BaseTool
from pcons.tools.toolchain import BaseToolchain, ToolchainContext

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from pcons.core.builder import Builder
    from pcons.core.environment import Environment
    from pcons.core.target import Target
    from pcons.core.toolconfig import ToolConfig
    from pcons.tools.toolchain import AuxiliaryInputHandler, SourceHandler


def _find_vswhere() -> Path | None:
    program_files = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    vswhere = (
        Path(program_files) / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
    )
    return vswhere if vswhere.exists() else None


def _find_msvc_install() -> Path | None:
    vswhere = _find_vswhere()
    if vswhere is None:
        return None
    try:
        result = subprocess.run(
            [
                str(vswhere),
                "-latest",
                "-requires",
                "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
                "-property",
                "installationPath",
            ],
            capture_output=True,
            text=True,
            timeout=30,
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
                "/c",
                TargetPath(prefix="/Fo"),
                SourcePath(),
            ],
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
            "libcmd": [
                "$lib.cmd",
                "$lib.flags",
                TargetPath(prefix="/OUT:"),
                SourcePath(),
            ],
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
        lib = config.find_program("lib.exe", version_flag="")
        if lib is None:
            return None
        from pcons.core.toolconfig import ToolConfig

        return ToolConfig("lib", cmd=str(lib.path))


class MsvcResourceCompiler(BaseTool):
    """MSVC resource compiler tool (rc.exe)."""

    def __init__(self) -> None:
        super().__init__("rc")

    def default_vars(self) -> dict[str, object]:
        return {
            "cmd": "rc.exe",
            "flags": ["/nologo"],
            "iprefix": "/I",
            "includes": [],
            "dprefix": "/D",
            "defines": [],
            "rccmd": [
                "$rc.cmd",
                "$rc.flags",
                "${prefix(rc.iprefix, rc.includes)}",
                "${prefix(rc.dprefix, rc.defines)}",
                TargetPath(prefix="/fo"),
                SourcePath(),
            ],
        }

    def builders(self) -> dict[str, Builder]:
        return {
            "Resource": CommandBuilder(
                "Resource",
                "rc",
                "rccmd",
                src_suffixes=[".rc"],
                target_suffixes=[".res"],
                single_source=True,
                deps_style=None,  # rc.exe doesn't generate depfiles
            ),
        }

    def configure(self, config: object) -> ToolConfig | None:
        from pcons.configure.config import Configure

        if not isinstance(config, Configure):
            return None
        platform = get_platform()
        if not platform.is_windows:
            return None

        # Try to find rc.exe in PATH first
        rc = config.find_program("rc.exe", version_flag="")
        if rc is None:
            # Look in Windows SDK
            program_files_x86 = os.environ.get(
                "ProgramFiles(x86)", r"C:\Program Files (x86)"
            )
            sdk_path = Path(program_files_x86) / "Windows Kits" / "10" / "bin"
            if sdk_path.exists():
                # Find the latest SDK version
                for version_dir in sorted(sdk_path.iterdir(), reverse=True):
                    if version_dir.is_dir() and version_dir.name.startswith("10."):
                        # Check architecture-specific paths
                        for arch in ["x64", "arm64", "x86"]:
                            rc_path = version_dir / arch / "rc.exe"
                            if rc_path.exists():
                                from pcons.configure.config import ProgramInfo

                                rc = ProgramInfo(path=rc_path)
                                break
                        if rc is not None:
                            break

        if rc is None:
            return None

        from pcons.core.toolconfig import ToolConfig

        return ToolConfig("rc", cmd=str(rc.path))


class MsvcAssembler(BaseTool):
    """MSVC macro assembler tool (ml64.exe for x64, ml.exe for x86).

    Variables:
        cmd: Assembler command (default: 'ml64.exe')
        flags: Assembler flags (list)
        iprefix: Include directory prefix (default: '/I')
        includes: Include directories (list of paths, no prefix)
        asmcmd: Command template for assembling to object
    """

    def __init__(self) -> None:
        super().__init__("ml")

    def default_vars(self) -> dict[str, object]:
        return {
            "cmd": "ml64.exe",
            "flags": ["/nologo"],
            "iprefix": "/I",
            "includes": [],
            "asmcmd": [
                "$ml.cmd",
                "$ml.flags",
                "${prefix(ml.iprefix, ml.includes)}",
                "/c",
                TargetPath(prefix="/Fo"),
                SourcePath(),
            ],
        }

    def builders(self) -> dict[str, Builder]:
        return {
            "AsmObject": CommandBuilder(
                "AsmObject",
                "ml",
                "asmcmd",
                src_suffixes=[".asm"],
                target_suffixes=[".obj"],
                language="asm",
                single_source=True,
                deps_style=None,  # MASM doesn't generate depfiles
            ),
        }

    def configure(self, config: object) -> ToolConfig | None:
        from pcons.configure.config import Configure

        if not isinstance(config, Configure):
            return None
        platform = get_platform()
        if not platform.is_windows:
            return None

        # Try to find ml64.exe (x64) first, then ml.exe (x86)
        ml = config.find_program("ml64.exe", version_flag="")
        if ml is None:
            ml = config.find_program("ml.exe", version_flag="")
        if ml is None:
            # Try to find in Visual Studio installation
            vs_path = _find_msvc_install()
            if vs_path:
                vc_tools = vs_path / "VC" / "Tools" / "MSVC"
                if vc_tools.exists():
                    for version_dir in sorted(vc_tools.iterdir(), reverse=True):
                        ml_path = version_dir / "bin" / "Hostx64" / "x64" / "ml64.exe"
                        if ml_path.exists():
                            from pcons.configure.config import ProgramInfo

                            ml = ProgramInfo(path=ml_path)
                            break

        if ml is None:
            return None

        from pcons.core.toolconfig import ToolConfig

        return ToolConfig("ml", cmd=str(ml.path))


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
                "$link.cmd",
                "$link.flags",
                TargetPath(prefix="/OUT:"),
                SourcePath(),
                "${prefix(link.Lprefix, link.libdirs)}",
                "$link.libs",
            ],
            "sharedcmd": [
                "$link.cmd",
                "/DLL",
                "$link.flags",
                TargetPath(prefix="/OUT:"),
                TargetPath(prefix="/IMPLIB:", index=1),
                SourcePath(),
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
                src_suffixes=[".obj", ".res"],
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
                    OutputSpec("export_file", ".exp", implicit=True),
                ],
                src_suffixes=[".obj", ".res"],
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

    # Flags that take their argument as a separate token for MSVC.
    # MSVC generally uses /FLAG:value syntax, so there are fewer separated arg flags.
    SEPARATED_ARG_FLAGS: frozenset[str] = frozenset(
        [
            # Linker passthrough (when invoking cl.exe which calls link.exe)
            "/link",
        ]
    )

    def __init__(self) -> None:
        super().__init__("msvc")

    # =========================================================================
    # Source Handler Methods
    # =========================================================================

    def get_source_handler(self, suffix: str) -> SourceHandler | None:
        """Return handler for source file suffix, or None if not handled."""
        from pcons.tools.toolchain import SourceHandler

        suffix_lower = suffix.lower()
        if suffix_lower == ".c":
            return SourceHandler("cc", "c", ".obj", None, "msvc")
        if suffix_lower in (".cpp", ".cxx", ".cc"):
            return SourceHandler("cxx", "cxx", ".obj", None, "msvc")
        if suffix_lower == ".rc":
            # Resource files compile to .res and have no depfile
            return SourceHandler("rc", "resource", ".res", None, None, "rccmd")
        if suffix_lower == ".asm":
            # MASM assembly files - compiled with ml64.exe (x64) or ml.exe (x86)
            return SourceHandler("ml", "asm", ".obj", None, None, "asmcmd")
        return None

    def get_auxiliary_input_handler(self, suffix: str) -> AuxiliaryInputHandler | None:
        """Return handler for auxiliary input files."""
        from pcons.tools.toolchain import AuxiliaryInputHandler

        suffix_lower = suffix.lower()
        if suffix_lower == ".def":
            return AuxiliaryInputHandler(".def", "/DEF:$file")
        if suffix_lower == ".manifest":
            # MSVC linker requires /MANIFEST:EMBED when using /MANIFESTINPUT
            return AuxiliaryInputHandler(
                ".manifest",
                "/MANIFESTINPUT:$file",
                extra_flags=["/MANIFEST:EMBED"],
            )
        return None

    def get_object_suffix(self) -> str:
        """Return the object file suffix for MSVC toolchain."""
        return ".obj"

    def get_archiver_tool_name(self) -> str:
        """Return the name of the archiver tool for MSVC toolchain."""
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

    def get_separated_arg_flags(self) -> frozenset[str]:
        """Return flags that take their argument as a separate token.

        Returns:
            A frozenset of MSVC flags that take separate arguments.
        """
        return self.SEPARATED_ARG_FLAGS

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

        rc = MsvcResourceCompiler()
        rc.configure(config)  # Optional - not required for toolchain to work

        ml = MsvcAssembler()
        ml.configure(config)  # Optional - not required for toolchain to work

        self._tools = {
            "cc": cc,
            "cxx": cxx,
            "lib": lib,
            "link": link,
            "rc": rc,
            "ml": ml,
        }
        return True

    # Architecture to MSVC machine type mapping
    MSVC_ARCH_MAP: dict[str, str] = {
        "x64": "X64",
        "x86": "X86",
        "arm64": "ARM64",
        "arm64ec": "ARM64EC",
        # Common aliases
        "amd64": "X64",
        "x86_64": "X64",
        "i386": "X86",
        "i686": "X86",
        "aarch64": "ARM64",
    }

    def apply_target_arch(self, env: Environment, arch: str, **kwargs: Any) -> None:
        """Apply target architecture flags for MSVC.

        Adds the /MACHINE:xxx flag to the linker. Note that for full
        cross-compilation support, you may also need to run vcvarsall.bat
        with the appropriate architecture argument, or use a cross-toolset.

        Supported architectures:
        - x64 (or amd64, x86_64): 64-bit Intel/AMD
        - x86 (or i386, i686): 32-bit Intel/AMD
        - arm64 (or aarch64): 64-bit ARM
        - arm64ec: ARM64EC emulation compatible

        Args:
            env: Environment to modify.
            arch: Architecture name.
            **kwargs: Toolchain-specific options (unused).
        """
        super().apply_target_arch(env, arch, **kwargs)
        machine = self.MSVC_ARCH_MAP.get(arch.lower(), arch.upper())

        # MSVC linker uses /MACHINE:xxx
        if env.has_tool("link"):
            if isinstance(env.link.flags, list):
                env.link.flags.append(f"/MACHINE:{machine}")

        # MSVC librarian also uses /MACHINE:xxx
        if env.has_tool("lib"):
            if isinstance(env.lib.flags, list):
                env.lib.flags.append(f"/MACHINE:{machine}")

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
        else:
            logger.warning("Unknown variant '%s', no flags applied", variant)

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

    def create_build_context(
        self,
        target: Target,
        env: Environment,
        for_compilation: bool = True,
    ) -> ToolchainContext | None:
        """Create a toolchain-specific build context for MSVC.

        Overrides the base implementation to use MsvcCompileLinkContext,
        which provides MSVC-style flag prefixes (/I, /D, /LIBPATH:).

        Args:
            target: The target being built.
            env: The build environment.
            for_compilation: If True, create context for compilation.
                            If False, create context for linking.

        Returns:
            A MsvcCompileLinkContext providing MSVC-formatted variables.
        """
        from pcons.core.requirements import compute_effective_requirements
        from pcons.toolchains.build_context import MsvcCompileLinkContext

        # Compute effective requirements
        effective = compute_effective_requirements(target, env, for_compilation)

        # Create and return MSVC-specific context
        return MsvcCompileLinkContext.from_effective_requirements(effective)


# =============================================================================
# Registration
# =============================================================================

from pcons.tools.toolchain import toolchain_registry  # noqa: E402

toolchain_registry.register(
    MsvcToolchain,
    aliases=["msvc", "vc", "visualstudio"],
    check_command="cl.exe",
    tool_classes=[
        MsvcCompiler,
        MsvcLibrarian,
        MsvcLinker,
        MsvcResourceCompiler,
        MsvcAssembler,
    ],
    category="c",
)
