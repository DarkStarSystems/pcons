# SPDX-License-Identifier: MIT
"""MSVC toolchain implementation (Windows only)."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from pcons.configure.platform import get_platform
from pcons.core.builder import CommandBuilder, MultiOutputBuilder, OutputSpec
from pcons.core.preset import ToolContribution
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


def _version_sort_key(name: str) -> tuple[int, ...] | None:
    """Parse a dot-separated version directory name into an int tuple.

    Handles both MSVC tool versions (``14.38.33130``) and Windows SDK
    versions (``10.0.22621.0``). Returns None if any component isn't a
    plain non-negative integer, so callers can skip garbage directory
    names instead of crashing.
    """
    parts = name.split(".")
    if not parts:
        return None
    try:
        return tuple(int(p) for p in parts)
    except ValueError:
        return None


def _sorted_version_dirs(parent: Path) -> list[Path]:
    """List subdirectories of `parent` sorted newest-version-first.

    Directory names are compared numerically component-by-component
    (e.g. "14.10" > "14.9"), unlike plain lexicographic sort. Entries
    that aren't dirs or don't parse as a version are skipped.
    """
    keyed = []
    for entry in parent.iterdir():
        if not entry.is_dir():
            continue
        key = _version_sort_key(entry.name)
        if key is None:
            continue
        keyed.append((key, entry))
    keyed.sort(key=lambda pair: pair[0], reverse=True)
    return [entry for _, entry in keyed]


def _host_arch_dirs() -> tuple[str, str]:
    """Return the (host, target) bin-dir names for the running host.

    Matches MSVC's `bin/Host<ARCH>/<arch>/` layout (e.g. `Hostx64/x64`),
    used both to locate the host-native toolset and as a same-arch
    fallback bin directory when no dev shell has been activated.
    """
    import platform as _platform

    machine = _platform.machine().lower()
    if machine in ("arm64", "aarch64"):
        return "HostARM64", "arm64"
    return "Hostx64", "x64"


def _find_msvc_modules_dir() -> Path | None:
    """Find the MSVC C++ standard library modules directory.

    Microsoft ships `std.ixx` and `std.compat.ixx` under
    `%VCToolsInstallDir%/modules/`. We try, in order:
        1. The VCToolsInstallDir env var (set by vcvars64.bat).
        2. vswhere → VC/Tools/MSVC/<version>/modules/.
    Returns None if no `std.ixx` is found.
    """
    env_root = os.environ.get("VCToolsInstallDir")
    if env_root:
        modules = Path(env_root) / "modules"
        if (modules / "std.ixx").exists():
            return modules

    vs_path = _find_msvc_install()
    if vs_path is None:
        return None
    vc_tools = vs_path / "VC" / "Tools" / "MSVC"
    if not vc_tools.exists():
        return None
    for version_dir in _sorted_version_dirs(vc_tools):
        modules = version_dir / "modules"
        if (modules / "std.ixx").exists():
            return modules
    return None


# ABI-affecting flags that must match between the std-module compile and
# user TUs that import it. The big-ticket items on MSVC are the runtime
# library (`/MD` vs `/MDd` etc. — Microsoft's STL changes ABI based on
# this), `_ITERATOR_DEBUG_LEVEL`, and `/Zc:*` conformance flags. Adapted
# from MSVC's STL configuration documentation; expand if a user reports
# a mismatch we missed.
def _msvc_std_module_flag_spec() -> Any:
    """Build the MSVC flag-passthrough spec lazily.

    Defined as a function to avoid circular imports between this module
    and ``cxx_module_scanner``.
    """
    from pcons.toolchains.cxx_module_scanner import StdModuleFlagSpec

    return StdModuleFlagSpec(
        # Runtime-library, exception model, RTTI, conformance, coroutines,
        # CLR — all flip ABI for Microsoft's STL.
        exact=frozenset(
            {
                "/MD",
                "/MDd",
                "/MT",
                "/MTd",
                "/EHs",
                "/EHsc",
                "/EHa",
                "/EHr",
                "/EHs-",
                "/EHsc-",
                "/EHa-",
                "/GR",
                "/GR-",
                "/permissive",
                "/permissive-",
                "/await",
                "/await:strict",
                "/clr",
                "/clr:pure",
                "/clr:safe",
                "/clr:netcore",
                "/bigobj",
            }
        ),
        # `/std:c++latest`, `/Zc:char8_t-`, `/arch:AVX2`, etc. — values
        # are attached to the prefix.
        prefixes=(
            "/std:",
            "/Zc:",
            "/arch:",
            "--target=",
        ),
        # MSVC very rarely uses GCC-style paired flags (clang-cl
        # accepts `--target X` though).
        paired=frozenset({"--target"}),
        # User defines that configure Microsoft's STL must propagate.
        # `_ITERATOR_DEBUG_LEVEL` and `_CONTAINER_DEBUG_LEVEL` must match
        # between std.ifc and consumers or you get heap-corrupting iter
        # mismatches. `_HAS_*` toggles language-version-conditional
        # features. `_CRT_*` configures the CRT itself. `_LIBCPP_*` is
        # included on the off-chance someone uses libc++ on Windows.
        define_prefix="/D",
        define_glob_prefixes=(
            "_HAS_",
            "_ITERATOR_DEBUG_LEVEL",
            "_CONTAINER_DEBUG_LEVEL",
            "_SECURE_SCL",
            "_CRT_",
            "_LIBCPP_",
        ),
    )


def _find_msvc_bin_dir() -> Path | None:
    """Find the MSVC bin directory via vswhere.

    Returns the path to the host-appropriate bin directory containing
    cl.exe, link.exe, lib.exe, etc., or None if not found.
    """
    vs_path = _find_msvc_install()
    if vs_path is None:
        return None
    vc_tools = vs_path / "VC" / "Tools" / "MSVC"
    if not vc_tools.exists():
        return None
    # Use the latest installed version
    host, target = _host_arch_dirs()
    for version_dir in _sorted_version_dirs(vc_tools):
        bin_dir = version_dir / "bin" / host / target
        if (bin_dir / "cl.exe").exists():
            return bin_dir
    return None


def find_msvc_toolset_version() -> str | None:
    """The installed MSVC toolset version, e.g. "14.44.35207".

    A vcvars shell sets VCToolsVersion; otherwise the newest toolset under
    the Visual Studio install vswhere finds. None when neither is available.
    """
    version = os.environ.get("VCToolsVersion")
    if version:
        return version
    vs_path = _find_msvc_install()
    if vs_path is None:
        return None
    vc_tools = vs_path / "VC" / "Tools" / "MSVC"
    if not vc_tools.exists():
        return None
    version_dirs = _sorted_version_dirs(vc_tools)
    return version_dirs[0].name if version_dirs else None


# Arch names as they appear as MSVC/SDK path components
# (bin/Host<host>/<arch>, lib/<arch>, Lib/<sdkver>/um/<arch>).
_ARCH_DIR_MAP: dict[str, str] = {
    "x64": "x64",
    "amd64": "x64",
    "x86_64": "x64",
    "x86": "x86",
    "i386": "x86",
    "i686": "x86",
    "arm64": "arm64",
    "aarch64": "arm64",
}


def _find_cross_toolset(target: str) -> tuple[Path, Path] | None:
    """Locate (bin_dir, vc_lib_dir) for the given target arch dir name.

    Searches VCToolsInstallDir (set by any vcvars shell) first, then
    vswhere; picks the newest toolset version whose bin/Host<host>/<target>
    contains the cross compiler.
    """
    host, _ = _host_arch_dirs()
    candidates: list[Path] = []
    env_root = os.environ.get("VCToolsInstallDir")
    if env_root:
        candidates.append(Path(env_root))
    vs_path = _find_msvc_install()
    if vs_path is not None:
        vc_tools = vs_path / "VC" / "Tools" / "MSVC"
        if vc_tools.exists():
            candidates.extend(_sorted_version_dirs(vc_tools))
    for version_dir in candidates:
        bin_dir = version_dir / "bin" / host / target
        if (bin_dir / "cl.exe").exists():
            return bin_dir, version_dir / "lib" / target
    return None


def _find_sdk_lib_dirs(target: str) -> list[Path]:
    """Windows SDK um/<arch> and ucrt/<arch> lib dirs, newest SDK first.

    Honors WindowsSdkDir/WindowsSDKLibVersion (set by a dev shell), falling
    back to the default Windows Kits install location.
    """
    sdk_root = os.environ.get("WindowsSdkDir")
    if sdk_root:
        lib_root = Path(sdk_root) / "Lib"
    else:
        program_files = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
        lib_root = Path(program_files) / "Windows Kits" / "10" / "Lib"

    version_dirs: list[Path] = []
    sdk_ver = os.environ.get("WindowsSDKLibVersion")  # e.g. "10.0.22621.0\"
    if sdk_ver:
        version_dir = lib_root / sdk_ver.strip("\\/")
        if version_dir.is_dir():
            version_dirs.append(version_dir)
    if not version_dirs and lib_root.is_dir():
        version_dirs = _sorted_version_dirs(lib_root)

    for version_dir in version_dirs:
        dirs = [version_dir / "um" / target, version_dir / "ucrt" / target]
        if all(d.is_dir() for d in dirs):
            return dirs
    return []


def _cross_lib_flags(target: str, vc_lib_dir: Path) -> tuple[str, ...]:
    """/LIBPATH: flags for a cross target: VC lib/<arch> + SDK um|ucrt/<arch>."""
    libdirs = [d for d in [vc_lib_dir, *_find_sdk_lib_dirs(target)] if d.is_dir()]
    if len(libdirs) < 3:
        logger.warning(
            "Incomplete %s library directories found (%s); the dev-shell LIB "
            "environment is host-arch and will not cover the %s target",
            target,
            ", ".join(str(d) for d in libdirs) or "none",
            target,
        )
    return tuple(f"/LIBPATH:{d}" for d in libdirs)


def cross_arch_contributions(
    arch: str, *, repoint_tools: bool
) -> list[ToolContribution]:
    """Contributions needed to target a cross *arch* on Windows.

    Shared by MSVC (which also repoints cl/link/lib at the cross toolset's
    binaries) and clang-cl (which retargets by flag but still needs the
    target's VC and Windows SDK libraries — the dev shell's LIB covers only
    the host arch). Returns nothing when *arch* is the native arch, unknown,
    or we're not on Windows; raises with install guidance when the cross
    toolset isn't installed.
    """
    if not get_platform().is_windows:
        return []
    target = _ARCH_DIR_MAP.get(arch.lower())
    host, native = _host_arch_dirs()
    if target is None or target == native:
        return []
    toolset = _find_cross_toolset(target)
    if toolset is None:
        raise ValueError(
            f"MSVC {target} cross toolset not found: no bin/{host}/{target}/"
            f"cl.exe in any installed VC tools version. Install the "
            f"'MSVC ... {target.upper()} build tools' component in the "
            f"Visual Studio Installer."
        )
    bin_dir, vc_lib_dir = toolset
    contribs: list[ToolContribution] = []
    if repoint_tools:
        for tool, exe in (
            ("cc", "cl.exe"),
            ("cxx", "cl.exe"),
            ("link", "link.exe"),
            ("lib", "lib.exe"),
        ):
            contribs.append(ToolContribution(tool, cmd=str(bin_dir / exe)))
    lib_flags = _cross_lib_flags(target, vc_lib_dir)
    if lib_flags:
        contribs.append(ToolContribution("link", flags=lib_flags))
    return contribs


class MsvcCompiler(BaseTool):
    """MSVC C/C++ compiler tool."""

    env_var = "CC"

    def __init__(self, name: str = "cc", language: str = "c") -> None:
        super().__init__(name, language=language)

    def default_vars(self) -> dict[str, object]:
        return {
            "cmd": "cl.exe",
            "flags": ["/nologo"],
            "iprefix": "/I",
            "includes": [],
            # /external:I needs /external:W0 to actually silence anything;
            # see MsvcToolchain.get_compile_flags_for_system_includes.
            "isysprefix": "/external:I",
            "system_includes": [],
            "dprefix": "/D",
            "defines": [],
            "depflags": ["/showIncludes"],
            "objcmd": [
                "$cc.cmd",
                "$cc.flags",
                "${prefix(cc.iprefix, cc.includes)}",
                "${prefix(cc.isysprefix, cc.system_includes)}",
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
            bin_dir = _find_msvc_bin_dir()
            if bin_dir is not None:
                cl_path = bin_dir / "cl.exe"
                if cl_path.exists():
                    from pcons.configure.config import ProgramInfo

                    cl = ProgramInfo(path=cl_path)

        if cl is None:
            return None

        from pcons.core.toolconfig import ToolConfig

        return ToolConfig(self._name, cmd=str(cl.path))


class MsvcCxxCompiler(MsvcCompiler):
    """MSVC C++ compiler tool (cxx namespace)."""

    env_var = "CXX"

    def __init__(self) -> None:
        super().__init__("cxx", "cxx")

    def default_vars(self) -> dict[str, object]:
        return {
            "cmd": "cl.exe",
            "flags": ["/nologo"],
            "iprefix": "/I",
            "includes": [],
            "isysprefix": "/external:I",
            "system_includes": [],
            "dprefix": "/D",
            "defines": [],
            # None = auto (module-suffix sources opt the env in); True also
            # scans module units in .cpp files; False disables scanning.
            "modules": None,
            "depflags": ["/showIncludes"],
            "objcmd": [
                "$cxx.cmd",
                "$cxx.flags",
                "${prefix(cxx.iprefix, cxx.includes)}",
                "${prefix(cxx.isysprefix, cxx.system_includes)}",
                "${prefix(cxx.dprefix, cxx.defines)}",
                "$cxx.depflags",
                "/c",
                TargetPath(prefix="/Fo"),
                SourcePath(),
            ],
            # Scanned TUs compile with a reference to their collate-written
            # modmap response file ($CXX_MODMAPREF is a per-edge variable —
            # "@<obj>.modmap" — so one rule serves every TU in a flag
            # class). cl.exe reads options from anywhere on the line.
            "modobjcmd": [
                "$cxx.cmd",
                "$cxx.flags",
                "${prefix(cxx.iprefix, cxx.includes)}",
                "${prefix(cxx.isysprefix, cxx.system_includes)}",
                "${prefix(cxx.dprefix, cxx.defines)}",
                "$cxx.depflags",
                "$CXX_MODMAPREF",
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

    env_var = "RC"

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

        rc = config.find_program("rc.exe", version_flag="")
        if rc is None:
            # Fall back to the Windows SDK install, newest version first
            program_files_x86 = os.environ.get(
                "ProgramFiles(x86)", r"C:\Program Files (x86)"
            )
            sdk_path = Path(program_files_x86) / "Windows Kits" / "10" / "bin"
            if sdk_path.exists():
                for version_dir in _sorted_version_dirs(sdk_path):
                    if version_dir.name.startswith("10."):
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
    """MSVC macro assembler tool (ml64.exe for x64, ml.exe for x86)."""

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

        # ml64.exe (x64) first, then ml.exe (x86)
        ml = config.find_program("ml64.exe", version_flag="")
        if ml is None:
            ml = config.find_program("ml.exe", version_flag="")
        if ml is None:
            # Fall back to the VS install (host-aware: ARM64 hosts get
            # HostARM64/arm64, not x64)
            bin_dir = _find_msvc_bin_dir()
            if bin_dir is not None:
                ml_path = bin_dir / "ml64.exe"
                if ml_path.exists():
                    from pcons.configure.config import ProgramInfo

                    ml = ProgramInfo(path=ml_path)

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
    """Microsoft Visual C++ toolchain (Windows only)."""

    ENV_COMPILER_FAMILY = "msvc"

    TOOL_NAMES = ("cc", "cxx", "lib", "link", "rc", "ml")

    def __init__(self) -> None:
        super().__init__("msvc")

    def _arch_contributions(self, arch: str) -> list[ToolContribution]:
        """Add /MACHINE: (via base) and, for a cross arch, repoint the tools.

        A cross arch on MSVC requires different binaries: the cross toolset
        lives at bin/Host<host>/<arch> in the same VC install, with matching
        lib/<arch> and Windows SDK um|ucrt/<arch> libraries (the dev
        shell's LIB covers only the host arch).
        """
        contribs = super()._arch_contributions(arch)
        contribs.extend(cross_arch_contributions(arch, repoint_tools=True))
        return contribs

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
            # No depfile (cl.exe doesn't produce make-style depfiles), but
            # do enable `deps = msvc` so ninja parses /showIncludes output —
            # otherwise #includes inside the module's global module fragment
            # (e.g. legacy headers a .cppm pulls in) aren't tracked and
            # touching one of those headers won't trigger a rebuild.
            # The cxx_modules.dyndep file handles inter-module ordering;
            # /showIncludes handles header deps. They're complementary.
            return SourceHandler("cxx", "cxx_module", ".obj", None, "msvc")
        return super().get_source_handler(suffix)

    def after_resolve(
        self,
        project: Project,
        source_obj_by_language: dict[str, list[tuple[Path, FileNode]]],
    ) -> None:
        """Set up C++20 module compilation (MSVC) via the Scanner.

        Configure records static facts only: which targets are scanned,
        each TU's compile flags and BMI-compatibility key, ``/TP`` on
        extension-tagged module units (``.cppm`` isn't a native MSVC C++
        extension). Content-derived facts — ``/interface`` vs
        ``/internalPartition``, ``/ifcOutput``, and explicit ``/reference
        name=path`` lines replacing ``/ifcSearchDir`` — flow through per-TU
        ``cl /scanDependencies`` edges and a per-target collate at build
        time, reaching each compile as a collate-written response file.
        Header deps ride ``/showIncludes`` on both the scan and the compile.
        """
        from pcons.core.scan import EdgeArgsSpec, Scanner, scope_id_for
        from pcons.core.subst import NodeVar, SourcePath, TargetPath
        from pcons.core.target import Target
        from pcons.toolchains.cxx_module_scanner import (
            bmi_key_for_flags,
            collect_module_scopes,
            merge_scan_compile_flags,
        )

        scopes = collect_module_scopes(project, source_obj_by_language, self)
        if not scopes:
            return
        flag_spec = _msvc_std_module_flag_spec()
        rel = project._path_resolver.make_execution_relative

        edge_facts: dict[int, dict[str, object]] = {}
        by_compiler: dict[str, list[object]] = {}
        envs: dict[int, Any] = {}
        env_keys: dict[int, set[str]] = {}
        target_keys: dict[int, set[str]] = {}

        for scope in scopes:
            env = scope.env
            envs[id(env)] = env
            cxx = getattr(env, "cxx", None)
            compiler = str(getattr(cxx, "cmd", "cl.exe") or "cl.exe")
            base_flags = list(getattr(cxx, "flags", None) or [])
            for _src, obj_node, is_module_suffix in scope.pairs:
                bi = obj_node._build_info
                if bi is None:
                    continue
                context = bi.get("context")
                # Suffix is a static fact: /TP so cl treats .cppm as C++
                # during scan and compile. /interface is NOT added here —
                # interface vs internal partition is the scan's call, and
                # the two are mutually exclusive (D8016).
                if (
                    is_module_suffix
                    and context is not None
                    and hasattr(context, "flags")
                    and "/TP" not in context.flags
                ):
                    context.flags.append("/TP")
                flags = merge_scan_compile_flags(
                    base_flags,
                    context,
                    iprefix="/I",
                    isysprefix="/external:I",
                    dprefix="/D",
                    root=project.root_dir,
                )
                obj_rel = rel(obj_node.path)
                key = bmi_key_for_flags(flags, flag_spec)
                edge_facts[id(obj_node)] = {
                    "flags": flags,
                    "key": key,
                    "module_suffix": is_module_suffix,
                    "obj_rel": obj_rel,
                }
                env_keys.setdefault(id(env), set()).add(key)
                target_keys.setdefault(id(scope.target), set()).add(key)

                bi["command_var"] = "modobjcmd"
                node_vars = bi.get("vars")
                if node_vars is None:
                    node_vars = {}
                    bi["vars"] = node_vars
                node_vars["CXX_MODMAPREF"] = "@" + obj_rel + ".modmap"
            by_compiler.setdefault(compiler, []).append(scope.target)

        # Dormant `import std;` edges per BMI key in use.
        std_exports: dict[int, dict[str, str]] = {}
        std_errors: dict[int, str | None] = {}
        for env_id, env in envs.items():
            exports_by_key, error = self._setup_std_modules(
                project, env, env_keys.get(env_id, set()), flag_spec
            )
            std_exports[env_id] = exports_by_key
            std_errors[env_id] = error

        def scan_vars(
            env: object, scanned: list[FileNode], governed: FileNode
        ) -> dict[str, object]:
            facts = edge_facts[id(governed)]
            return {
                "SCAN_FLAGS": list(cast("list[str]", facts["flags"])),
                # /Fo takes the joined form; the p1689 primary-output is
                # named after it, so the scan must know the real object.
                "SCAN_FO": f"/Fo{facts['obj_rel']}",
            }

        def edge_extra(
            env: object, scanned: list[FileNode], governed: FileNode
        ) -> dict[str, object]:
            facts = edge_facts[id(governed)]
            return {
                "key": facts["key"],
                "module_suffix": facts["module_suffix"],
            }

        def manifest_extra(env: object, target: object) -> dict[str, object]:
            by_key = std_exports.get(id(env), {})
            keys = target_keys.get(id(target), set())
            extra: dict[str, object] = {
                "style": "msvc",
                "bmi_ext": ".ifc",
                "moddir": f"cxx_modules/{scope_id_for(cast(Target, target))}",
                "std_exports": sorted(by_key[k] for k in keys if k in by_key),
            }
            error = std_errors.get(id(env))
            if error:
                extra["std_error"] = error
            return extra

        cxx_suffixes = tuple(
            sorted(
                suffix
                for suffix in self.source_suffixes()
                if (handler := self.get_source_handler(suffix)) is not None
                and handler.language in ("cxx", "cxx_module")
            )
        )

        for compiler, targets in by_compiler.items():
            scanner = Scanner(
                "cxx-modules",
                source_suffixes=cxx_suffixes,
                # cl scans without compiling; /showIncludes still reports
                # the headers read, which ninja stores in its deps log.
                scan_command=[
                    compiler,
                    "/nologo",
                    NodeVar("SCAN_FLAGS"),
                    "/TP",
                    "/showIncludes",
                    "/scanDependencies",
                    TargetPath(),
                    NodeVar("SCAN_FO"),
                    "/c",
                    SourcePath(),
                ],
                info_suffix=".ddi",
                scan_deps_style="msvc",
                scan_vars=scan_vars,
                edge_extra=edge_extra,
                manifest_extra=manifest_extra,
                collate_command=[
                    sys.executable,
                    "-m",
                    "pcons.toolchains.cxx_collate",
                    "--manifest",
                    NodeVar("SCAN_MANIFEST"),
                ],
                # The modmap reference lives in the modobjcmd template as a
                # per-edge variable; nothing is appended.
                edge_args=EdgeArgsSpec(suffix=".modmap", var=None, token=None),
                # Extra link inputs collate discovers (the std module's
                # object): link.exe reads @file response files natively.
                link_args=EdgeArgsSpec(
                    suffix=".linkextras.rsp",
                    var="CXX_LINKEXTRAS",
                    token="@$CXX_LINKEXTRAS",
                ),
                link_args_target_types=("program", "shared_library"),
            )
            scanner.attach(*cast("list[Target]", targets))

    def _setup_std_modules(
        self,
        project: Project,
        env: Any,
        keys: set[str],
        flag_spec: Any,
    ) -> tuple[dict[str, str], str | None]:
        """Describe dormant `import std;` build edges for *keys* (MSVC).

        Same contract as the LLVM and GCC versions: the edges appear in the
        build file and run only when some TU's collate discovers a real
        `import std;`. Microsoft's STL is very ABI-sensitive (/MD vs /MDd,
        _ITERATOR_DEBUG_LEVEL), so the std IFC is keyed by the same
        BMI-sensitive flags its importers use and gated on a key actually
        in use.

        Returns ``(exports_by_key, error_text)``.
        """
        import json as _json

        from pcons.core.collate import write_text_if_changed
        from pcons.toolchains.cxx_module_scanner import (
            bmi_key_for_flags,
            select_std_module_flags,
        )

        cxx = getattr(env, "cxx", None)
        compiler_cmd = str(getattr(cxx, "cmd", "cl.exe") or "cl.exe")
        base_flags = list(getattr(cxx, "flags", None) or [])

        std_modules_dir = _find_msvc_modules_dir()
        if std_modules_dir is None:
            return {}, (
                "`import std;` was used, but pcons could not locate "
                "Microsoft's STL modules directory. It expects "
                "`%VCToolsInstallDir%/modules/std.ixx` to exist; ensure "
                "VCToolsInstallDir is set (typically by running "
                "vcvars64.bat) or that vswhere can locate the VS install."
            )

        env_defines = list(getattr(cxx, "defines", None) or [])
        dprefix = str(getattr(cxx, "dprefix", "/D") or "/D")
        all_user_flags = list(base_flags) + [f"{dprefix}{d}" for d in env_defines]
        passthrough = select_std_module_flags(all_user_flags, flag_spec)
        if not any(f.startswith("/std:") for f in passthrough):
            passthrough.insert(0, "/std:c++latest")
        if not any(f in {"/EHs", "/EHsc", "/EHa"} for f in passthrough):
            passthrough.append("/EHsc")

        std_key = bmi_key_for_flags(passthrough, flag_spec)
        if std_key not in keys:
            return {}, None

        build_dir = project.build_dir
        build_dir_fs = (
            build_dir if build_dir.is_absolute() else project.root_dir / build_dir
        )
        std_moddir = f"cxx_modules/std/{std_key}"
        (build_dir_fs / std_moddir).mkdir(parents=True, exist_ok=True)

        exports_modules: dict[str, dict[str, object]] = {}
        prev_ifc_node: FileNode | None = None
        for logical in ("std", "std.compat"):
            ixx_name = "std.ixx" if logical == "std" else "std.compat.ixx"
            ixx_path = std_modules_dir / ixx_name
            if not ixx_path.exists():
                logger.warning(
                    "%s does not exist; skipping the %s module",
                    ixx_path,
                    logical,
                )
                continue

            ifc_rel = f"{std_moddir}/{logical}.ifc"
            obj_rel = f"{std_moddir}/{logical}.obj"
            std_obj_node = project.node(build_dir / obj_rel)
            ifc_node = project.node(build_dir / ifc_rel)
            std_obj_node._build_info = {
                "tool": "cxx",
                "command_var": "stdmodcmd",
                "description": f"CXX {logical} module",
                "sources": [project.node(ixx_path)],
                "command": [
                    compiler_cmd,
                    "/nologo",
                    *passthrough,
                    "/c",
                    # std.compat imports std; let it find the keyed std.ifc.
                    "/ifcSearchDir",
                    std_moddir,
                    "/TP",
                    "/interface",
                    "/ifcOutput",
                    ifc_rel,
                    f"/Fo{obj_rel}",
                    str(ixx_path).replace("\\", "/"),
                ],
                "outputs": {
                    "obj": {"path": std_obj_node.path, "implicit": False},
                    "ifc": {"path": ifc_node.path, "implicit": True},
                },
            }
            ifc_node._build_info = {"primary_node": std_obj_node}
            if prev_ifc_node is not None:
                std_obj_node.depends(prev_ifc_node)
            env.register_node(std_obj_node)
            env.register_node(ifc_node)
            prev_ifc_node = ifc_node

            exports_modules[logical] = {
                "bmi": ifc_rel,
                "key": std_key,
                "obj": obj_rel,
                "is_interface": True,
                "requires": ["std"] if logical == "std.compat" else [],
            }

        if not exports_modules:
            return {}, None
        exports_rel = f"cxx_modules/std/{std_key}.exports.json"
        write_text_if_changed(
            build_dir_fs / exports_rel,
            _json.dumps(
                {
                    "version": 1,
                    "scanner": "cxx-modules",
                    "scope": f"std/{std_key}",
                    "modules": exports_modules,
                },
                indent=1,
                sort_keys=True,
            )
            + "\n",
        )
        return {std_key: exports_rel}, None

    def _variant_contributions(
        self, variant: str, **kwargs: Any
    ) -> list[ToolContribution]:
        """MSVC variant flags, plus the /DEBUG linker flag for debug builds."""
        contribs = super()._variant_contributions(variant, **kwargs)
        if variant.lower() in ("debug", "relwithdebinfo"):
            contribs.append(ToolContribution("link", flags=("/DEBUG",)))
        return contribs

    def create_build_context(
        self,
        target: Target,
        env: Environment,
        for_compilation: bool = True,
    ) -> ToolchainContext | None:
        """Create an MsvcCompileLinkContext (/I, /D, /LIBPATH: prefixes)."""
        from pcons.toolchains.build_context import MsvcCompileLinkContext
        from pcons.tools.requirements import compute_effective_requirements

        effective = compute_effective_requirements(target, env, for_compilation)

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
