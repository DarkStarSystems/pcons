# SPDX-License-Identifier: MIT
"""MSVC toolchain implementation (Windows only)."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pcons.configure.platform import get_platform
from pcons.core.builder import CommandBuilder, MultiOutputBuilder, OutputSpec
from pcons.core.subst import SourcePath, TargetPath
from pcons.toolchains._msvc_compat import MsvcCompatibleToolchain
from pcons.tools.tool import BaseTool
from pcons.tools.toolchain import ToolchainContext

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from pcons.core.builder import Builder
    from pcons.core.environment import Environment
    from pcons.core.node import FileNode
    from pcons.core.project import Project
    from pcons.core.target import Target
    from pcons.core.toolconfig import ToolConfig
    from pcons.tools.toolchain import SourceHandler


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


class MsvcCxxCompiler(MsvcCompiler):
    """MSVC C++ compiler tool (cxx namespace)."""

    def __init__(self) -> None:
        super().__init__("cxx", "cxx")

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
                "$cxx.cmd",
                "$cxx.flags",
                "${prefix(cxx.iprefix, cxx.includes)}",
                "${prefix(cxx.dprefix, cxx.defines)}",
                "$cxx.depflags",
                "/c",
                TargetPath(prefix="/Fo"),
                SourcePath(),
            ],
        }


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
                TargetPath(prefix="/OUT:", index=0),
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


class MsvcToolchain(MsvcCompatibleToolchain):
    """Microsoft Visual C++ toolchain (Windows only).

    Inherits common MSVC-compatible functionality from MsvcCompatibleToolchain.
    """

    def __init__(self) -> None:
        super().__init__("msvc")

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

        cxx = MsvcCxxCompiler()
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

    def get_source_handler(self, suffix: str) -> SourceHandler | None:
        """Extend base MSVC handler to support C++20 module interface units (.cppm)."""
        from pcons.tools.toolchain import SourceHandler

        if suffix == ".cppm":
            # No depfile for module interfaces: module deps are handled by dyndep.
            return SourceHandler("cxx", "cxx_module", ".obj", None, None)
        return super().get_source_handler(suffix)

    def after_resolve(
        self,
        project: Project,
        source_obj_by_language: dict[str, list[tuple[Path, FileNode]]],
    ) -> None:
        """Set up Ninja dyndep for C++20 module dependencies (MSVC).

        Mirrors LlvmToolchain.after_resolve() but uses MSVC flags:
        - /scanDependencies:<file> for dependency scanning (P1689R5)
        - /interface /ifcOutput <path> to produce .ifc alongside .obj
        - /ifcSearchDir <dir> so importers can find precompiled modules
        """
        cxx_module_pairs = source_obj_by_language.get("cxx_module", [])
        if not cxx_module_pairs:
            return

        cxx_pairs = source_obj_by_language.get("cxx", [])
        all_cxx_pairs = cxx_module_pairs + cxx_pairs

        build_dir = project.build_dir
        moddir = "cxx_modules"
        manifest_path = build_dir / "cxx.manifest.json"
        dyndep_path = build_dir / "cxx_modules.dyndep"

        # Get environment and compiler command from first cxx_module object node
        first_env = None
        _, first_obj = cxx_module_pairs[0]
        build_info = getattr(first_obj, "_build_info", None)
        if build_info:
            first_env = build_info.get("env")

        cxx_tool = getattr(first_env, "cxx", None) if first_env else None
        compiler_cmd = str(getattr(cxx_tool, "cmd", "cl.exe") or "cl.exe")
        base_flags = list(getattr(cxx_tool, "flags", None) or [])

        build_dir.mkdir(parents=True, exist_ok=True)
        (build_dir / moddir).mkdir(exist_ok=True)

        # Build manifest entries
        manifest = []
        for src, obj_node in all_cxx_pairs:
            bi = getattr(obj_node, "_build_info", None)
            context = bi.get("context") if bi else None
            is_module_interface = src.suffix == ".cppm"

            # Combine base flags + effective requirement flags (MSVC-style prefixes)
            seen: set[str] = set(base_flags)
            compile_flags = list(base_flags)
            if context:
                for f in context.flags:
                    if f not in seen:
                        compile_flags.append(f)
                        seen.add(f)
                for inc in context.includes:
                    compile_flags.append(f"/I{inc}")
                for d in context.defines:
                    compile_flags.append(f"/D{d}")

            # For module interfaces, /TP and /interface are needed so the
            # scanner (cl.exe /scanDependencies) detects what module is provided.
            scan_flags = list(compile_flags)
            if is_module_interface and "/interface" not in scan_flags:
                scan_flags = ["/TP", "/interface"] + scan_flags

            entry: dict[str, object] = {
                "src": str(src.resolve()).replace("\\", "/"),
                "obj": str(obj_node.path.relative_to(build_dir)).replace("\\", "/"),
                "is_module_interface": is_module_interface,
                "compiler": compiler_cmd,
                "compile_flags": scan_flags,
            }
            if is_module_interface:
                entry["ifc"] = f"{moddir}/{src.stem}.ifc"
            manifest.append(entry)

        manifest_path.write_text(json.dumps(manifest, indent=2))
        logger.debug("Wrote C++ module manifest to %s", manifest_path)

        # Inject /ifcSearchDir into ALL cxx/cxx_module compile contexts
        for _, obj_node in all_cxx_pairs:
            bi = getattr(obj_node, "_build_info", None)
            if bi:
                context = bi.get("context")
                if context is not None and hasattr(context, "flags"):
                    if "/ifcSearchDir" not in context.flags:
                        context.flags.extend(["/ifcSearchDir", moddir])

        # Inject /interface /ifcOutput for module interface files
        for src, obj_node in cxx_module_pairs:
            bi = getattr(obj_node, "_build_info", None)
            if bi:
                context = bi.get("context")
                if context is not None and hasattr(context, "flags"):
                    ifc_path = f"{moddir}/{src.stem}.ifc"
                    if "/interface" not in context.flags:
                        # /TP forces C++ compilation for .cppm (unrecognized extension)
                        context.flags.extend(
                            ["/TP", "/interface", "/ifcOutput", ifc_path]
                        )

        # Create source file nodes for scanner dependencies
        all_sources = [src for src, _ in all_cxx_pairs]
        source_nodes = [project.node(src) for src in all_sources]

        # Create the dyndep scanner build node
        dyndep_node = project.node(dyndep_path)
        dyndep_node.depends(source_nodes)
        dyndep_node._build_info = {
            "tool": "cxx_scanner",
            "command_var": "scancmd",
            "description": "SCAN C++ modules",
            "sources": source_nodes,
            "command": [
                sys.executable,
                "-m",
                "pcons.toolchains.cxx_module_scanner",
                "--manifest",
                "cxx.manifest.json",
                "--out",
                "cxx_modules.dyndep",
                "--mod-dir",
                moddir,
                "--scanner-style",
                "msvc",
                "--scanner",
                compiler_cmd,
            ],
        }

        # Register dyndep node with environment
        if first_env is not None:
            first_env.register_node(dyndep_node)

        # Attach dyndep to all CXX and CXX_MODULE object nodes
        dyndep_rel = "cxx_modules.dyndep"
        for _, obj_node in all_cxx_pairs:
            bi = getattr(obj_node, "_build_info", None)
            if bi is not None:
                bi["dyndep"] = dyndep_rel
            obj_node.implicit_deps.append(dyndep_node)

    def apply_variant(self, env: Environment, variant: str, **kwargs: Any) -> None:
        """Apply build variant with MSVC flags.

        Extends base class to add /DEBUG linker flag for debug variants.
        """
        # Base class handles compile flags and defines
        super().apply_variant(env, variant, **kwargs)

        # MSVC also needs /DEBUG linker flag for debug variants
        variant_lower = variant.lower()
        if variant_lower in ("debug", "relwithdebinfo"):
            if env.has_tool("link") and isinstance(env.link.flags, list):
                env.link.flags.append("/DEBUG")

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
        mode = "compile" if for_compilation else "link"
        return MsvcCompileLinkContext.from_effective_requirements(
            effective, mode=mode,
        )


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
        MsvcCxxCompiler,
        MsvcLibrarian,
        MsvcLinker,
        MsvcResourceCompiler,
        MsvcAssembler,
    ],
    category="c",
    platforms=["win32"],
    description="Microsoft Visual C/C++ compiler",
    finder="find_c_toolchain()",
)
