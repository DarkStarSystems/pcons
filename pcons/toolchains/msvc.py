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
from pcons.toolchains._msvc_compat import MsvcCompatibleToolchain
from pcons.tools.tool import BaseTool
from pcons.tools.toolchain import CXX_MODULE_INTERFACE_SUFFIXES, ToolchainContext

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


def _find_msvc_bin_dir() -> Path | None:
    """Find the MSVC bin directory via vswhere.

    Returns the path to the host-appropriate bin directory containing
    cl.exe, link.exe, lib.exe, etc., or None if not found.
    """
    import platform as _platform

    vs_path = _find_msvc_install()
    if vs_path is None:
        return None
    vc_tools = vs_path / "VC" / "Tools" / "MSVC"
    if not vc_tools.exists():
        return None
    # Use the latest installed version
    machine = _platform.machine().lower()
    if machine in ("arm64", "aarch64"):
        host = "HostARM64"
        target = "arm64"
    else:
        host = "Hostx64"
        target = "x64"
    for version_dir in sorted(vc_tools.iterdir(), reverse=True):
        bin_dir = version_dir / "bin" / host / target
        if (bin_dir / "cl.exe").exists():
            return bin_dir
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

    def setup(self, env: Environment) -> None:
        """Set up MSVC tools, resolving full paths when needed.

        Handles two cases:
        1. cl.exe is in PATH but link.exe resolves to the wrong binary
           (e.g. Git's /usr/bin/link.exe shadows MSVC's link.exe).
           Emits full path only for the shadowed tool.
        2. cl.exe is not in PATH at all (not a VS Developer shell).
           Warns and emits full paths for all MSVC tools via vswhere.
        """
        import shutil

        super().setup(env)

        cl_which = shutil.which("cl.exe")
        if cl_which is not None:
            cl_dir = Path(cl_which).parent
            # Check if link.exe and lib.exe resolve to the same dir as cl.exe
            for tool_name, exe_name in [("link", "link.exe"), ("lib", "lib.exe")]:
                tool_which = shutil.which(exe_name)
                if tool_which is not None and Path(tool_which).parent == cl_dir:
                    continue  # Correct tool, nothing to do
                # Wrong tool or not found — use the one next to cl.exe
                correct_path = cl_dir / exe_name
                if correct_path.exists():
                    logger.warning(
                        "%s in PATH is not the MSVC one (expected in %s). "
                        "Using full path: %s",
                        exe_name,
                        cl_dir,
                        correct_path,
                    )
                    env.add_tool(tool_name).set("cmd", str(correct_path))
        else:
            # cl.exe not in PATH — try vswhere
            bin_dir = _find_msvc_bin_dir()
            if bin_dir is None:
                return
            logger.warning(
                "MSVC found via vswhere at %s but cl.exe is not in PATH. "
                "Consider running from a Visual Studio Developer shell "
                "for full SDK support (headers, libraries, rc.exe, etc.).",
                bin_dir,
            )
            tool_exes = {
                "cc": "cl.exe",
                "cxx": "cl.exe",
                "link": "link.exe",
                "lib": "lib.exe",
            }
            for tool_name, exe_name in tool_exes.items():
                full_path = bin_dir / exe_name
                if full_path.exists():
                    env.add_tool(tool_name).set("cmd", str(full_path))

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
        """Extend base MSVC handler to recognize C++20 module interface units."""
        from pcons.tools.toolchain import SourceHandler

        if suffix in CXX_MODULE_INTERFACE_SUFFIXES:
            # No depfile for module interfaces: module deps are handled by dyndep.
            return SourceHandler("cxx", "cxx_module", ".obj", None, None)
        return super().get_source_handler(suffix)

    def after_resolve(
        self,
        project: Project,
        source_obj_by_language: dict[str, list[tuple[Path, FileNode]]],
    ) -> None:
        """Configure C++20 module compilation (MSVC).

        Runs `cl.exe /scanDependencies` at configure time on every C++ TU in
        any target that uses modules, then drives flag injection from the
        scan output rather than from file extension. This handles partition
        units that live in `.cpp` files (interface partitions and internal
        partitions both) and lets us inject `/internalPartition` exactly
        when the scanner reports `provides[].is-interface == false`.

        Per-TU flag injection:
          - module-providing TU: `/TP /interface /ifcOutput <ifc>`
          - internal partition unit (provides + is-interface=false):
            also `/internalPartition`
        Project-wide:
          - `/ifcSearchDir <moddir>` on every C++ TU so importers find IFCs

        The Ninja dyndep file is written directly here (no build-time scan
        rule). Its provides/requires come from the scan output and use IFC
        paths derived from logical module names (so partitions resolve).
        """
        from pcons.toolchains.cxx_module_scanner import (
            TuScanSpec,
            build_module_map,
            scan_translation_units,
            write_dyndep_from_results,
        )

        cxx_module_pairs = source_obj_by_language.get("cxx_module", [])
        if not cxx_module_pairs:
            # No file-extension-tagged module units — skip scanning entirely.
            # Targets that put partition units only in .cpp files won't be
            # picked up; users must include at least one .cppm/.ixx/etc.
            return

        cxx_pairs = source_obj_by_language.get("cxx", [])
        all_cxx_pairs = cxx_module_pairs + cxx_pairs

        build_dir = project.build_dir
        moddir = "cxx_modules"
        dyndep_path = build_dir / "cxx_modules.dyndep"
        dyndep_rel = "cxx_modules.dyndep"

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

        # Inject /ifcSearchDir on all C++ TUs so importers find IFCs.
        for _, obj_node in all_cxx_pairs:
            bi = getattr(obj_node, "_build_info", None)
            if bi:
                context = bi.get("context")
                if context is not None and hasattr(context, "flags"):
                    if "/ifcSearchDir" not in context.flags:
                        context.flags.extend(["/ifcSearchDir", moddir])

        # Pre-flag every extension-tagged module unit with /TP so cl.exe
        # treats the file as C++ during scan and compile (.cppm isn't a
        # native MSVC C++ extension; .ixx is). We deliberately do NOT add
        # /interface here — that's a per-TU decision driven by the scan
        # output (interface units get /interface, internal partition
        # implementations get /internalPartition; the two are incompatible).
        for _, obj_node in cxx_module_pairs:
            bi = getattr(obj_node, "_build_info", None)
            if bi:
                context = bi.get("context")
                if context is not None and hasattr(context, "flags"):
                    if "/TP" not in context.flags:
                        context.flags.append("/TP")

        # Build per-TU scan specs using the now-flag-injected compile flags.
        specs: list[TuScanSpec] = []
        for src, obj_node in all_cxx_pairs:
            bi = getattr(obj_node, "_build_info", None)
            context = bi.get("context") if bi else None
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

            specs.append(
                TuScanSpec(
                    src=src.resolve(),
                    obj_rel=str(obj_node.path.relative_to(build_dir)).replace(
                        "\\", "/"
                    ),
                    compiler=compiler_cmd,
                    compile_flags=compile_flags,
                )
            )

        # Run cl.exe /scanDependencies on each TU. Failures (e.g. compiler
        # not on PATH) leave that result with p1689=None — propagated as
        # "not a module provider" so the build doesn't get an /ifcOutput it
        # can't satisfy. Errors are logged to stderr by the runner.
        results = scan_translation_units(
            specs, scanner=compiler_cmd, scanner_style="msvc"
        )

        # Build logical-name -> IFC path map (handles partitions: ':' -> '-').
        module_to_ifc = build_module_map(results, moddir, ".ifc")

        # Now inject per-TU module flags driven by the scan output.
        # A TU is a module provider iff scan reports a non-empty provides[].
        # An *internal* partition (is-interface=false) needs /internalPartition.
        spec_to_obj = {
            id(spec): obj_node
            for spec, (_, obj_node) in zip(specs, all_cxx_pairs, strict=True)
        }
        for r in results:
            if not r.is_module_provider:
                continue
            obj_node = spec_to_obj[id(r.spec)]
            bi = getattr(obj_node, "_build_info", None)
            if bi is None:
                continue
            context = bi.get("context")
            if context is None or not hasattr(context, "flags"):
                continue
            ifc_path = module_to_ifc[r.logical_name]
            if "/ifcOutput" in context.flags:
                continue
            # /interface and /internalPartition are mutually exclusive (D8016).
            # Choose based on whether the scanner reported this as an interface.
            if "/TP" not in context.flags:
                context.flags.append("/TP")
            if r.is_interface:
                context.flags.extend(["/interface", "/ifcOutput", ifc_path])
            else:
                context.flags.extend(["/internalPartition", "/ifcOutput", ifc_path])

        # Write the dyndep file at configure time and reference it as an
        # implicit input on each obj. No build-time scan rule needed.
        write_dyndep_from_results(results, module_to_ifc, dyndep_path)
        logger.debug("Wrote C++ module dyndep to %s", dyndep_path)

        dyndep_node = project.node(dyndep_path)
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
        from pcons.toolchains.build_context import MsvcCompileLinkContext
        from pcons.tools.requirements import compute_effective_requirements

        # Compute effective requirements
        effective = compute_effective_requirements(target, env, for_compilation)

        # Create and return MSVC-specific context
        mode = "compile" if for_compilation else "link"
        return MsvcCompileLinkContext.from_effective_requirements(
            effective,
            mode=mode,
        )


# =============================================================================
# Registration
# =============================================================================

from pcons.tools.toolchain import toolchain_registry  # noqa: E402


def _is_msvc_available() -> bool:
    """Check if MSVC is available, either in PATH or via vswhere."""
    import shutil

    return shutil.which("cl.exe") is not None or _find_msvc_install() is not None


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
    is_available=_is_msvc_available,
)
