# SPDX-License-Identifier: MIT
"""Intel Fortran (ifx) toolchains: GNU-style on Linux, MSVC-style on Windows.

ifx needs the oneAPI environment (``source /opt/intel/oneapi/setvars.sh``,
or ``setvars.bat`` on Windows) both to be found and for its programs to
find its shared runtime. On Windows that environment is also where ifx
finds its intrinsic modules (``INCLUDE``).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

from pcons.core.subst import SourcePath, TargetPath
from pcons.toolchains._msvc_compat import MsvcCompatibleToolchain
from pcons.toolchains.fortran import (
    DEFAULT_MODDIR,
    FortranCompiler,
    FortranLinker,
    FortranToolchain,
    FortranToolchainBase,
)
from pcons.toolchains.gcc import GccArchiver
from pcons.toolchains.msvc import MsvcLibrarian, MsvcLinker, use_msvc_tool_paths

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pcons.core.environment import Environment
    from pcons.core.flags import FlagToken
    from pcons.core.target import Target


class IfxCompiler(FortranCompiler):
    """Intel Fortran compiler tool."""

    COMMANDS = ("ifx",)
    MODDIR_FLAG = "-module"


class IfxLinker(FortranLinker):
    """Linker using ifx as the driver, for Fortran runtime linkage."""

    COMMANDS = ("ifx",)


class IfxToolchain(FortranToolchain):
    """Intel Fortran toolchain: ifx, ar, ifx as linker."""

    TOOL_NAMES = ("fc", "ar", "link")
    ENV_COMPILER_FAMILY = "ifx"

    COMPILER = IfxCompiler
    ARCHIVER = GccArchiver
    LINKER = IfxLinker
    RUNTIME_PROBE_FILE = "libifcore.a"
    # In the order ifx itself links them.
    RUNTIME_LIBS = ("ifport", "ifcoremt", "imf", "svml", "irc")

    FEATURE_PRESETS: dict[str, dict[str, list[str]]] = {
        "warnings": {"compile_flags": ["-warn", "all"]},
        "werror": {"compile_flags": ["-warn", "errors"]},
        "openmp": {"compile_flags": ["-qopenmp"], "link_flags": ["-qopenmp"]},
        "fast-math": {"compile_flags": ["-ffast-math"], "link_flags": ["-ffast-math"]},
    }

    def __init__(self) -> None:
        super().__init__("ifx")

    def get_compile_flags_for_target_type(
        self, target_type: str, env: Environment | None = None
    ) -> list[str]:
        """Position-independent code for every target type.

        Unlike GCC and Clang on current Linux distributions, ifx doesn't
        default to it, and a C/C++ driver's (PIE) link rejects its objects.
        """
        return super().get_compile_flags_for_target_type(target_type, env) or ["-fPIE"]


class IfxMsvcCompiler(FortranCompiler):
    """Intel Fortran compiler tool, with Windows (MSVC-style) options."""

    COMMANDS = ("ifx",)
    MODDIR_FLAG = "/module:"

    def default_vars(self) -> dict[str, object]:
        return {
            "cmd": self.COMMANDS[0],
            "flags": ["/nologo"],
            "iprefix": "/I",
            "includes": [],
            "dprefix": "/D",
            "defines": [],
            "moddir": DEFAULT_MODDIR,
            "submodule_file": self.SUBMODULE_FILE,
            "ancestor_file": self.ANCESTOR_FILE,
            "objcmd": [
                "$fc.cmd",
                "$fc.flags",
                "${prefix(fc.iprefix, fc.includes)}",
                "${prefix(fc.dprefix, fc.defines)}",
                f"{self.MODDIR_FLAG}$fc.moddir",
                "/I",
                "$fc.moddir",
                "/c",
                TargetPath(prefix="/Fo"),
                SourcePath(),
            ],
        }


class IfxMsvcToolchain(FortranToolchainBase, MsvcCompatibleToolchain):
    """Intel Fortran on Windows: ifx, lib.exe, link.exe.

    MSVC ABI, so it mixes with MSVC and clang-cl C/C++. Its objects name
    their runtime libraries (/DEFAULTLIB), so a link needs only the
    directory they are in.
    """

    TOOL_NAMES = ("fc", "lib", "link")
    ENV_COMPILER_FAMILY = "ifx"

    COMPILER = IfxMsvcCompiler
    ARCHIVER = MsvcLibrarian
    LINKER = MsvcLinker
    RUNTIME_LIBS = ()

    FEATURE_PRESETS: dict[str, dict[str, list[str]]] = {
        "warnings": {"compile_flags": ["/warn:all"]},
        "werror": {"compile_flags": ["/warn:errors"]},
        # The objects name the OpenMP runtime (/DEFAULTLIB), as cl's do.
        "openmp": {"compile_flags": ["/Qopenmp"]},
        "fast-math": {"compile_flags": ["/fp:fast"]},
    }

    # MsvcToolchain's, so Fortran and C/C++ objects agree on one C runtime
    # (/MD(d) also selects Intel's DLL runtime), with ifx's own top level.
    MSVC_VARIANTS = {
        **MsvcCompatibleToolchain.MSVC_VARIANTS,
        "release-fastest": (["/O3", "/MD"], ["NDEBUG"]),
    }

    def __init__(self) -> None:
        super().__init__("ifx")

    @property
    def objects_name_runtimes(self) -> bool:
        return True

    def setup(self, env: Environment) -> None:
        """Set up ifx's tools, with link.exe and lib.exe by full path when
        PATH would find another program first (Git's link.exe). Only tools
        this toolchain added: an existing linker (clang-cl's lld-link,
        say) stays."""
        before = set(env.tool_names())
        super().setup(env)
        exes = {"link": "link.exe", "lib": "lib.exe"}
        use_msvc_tool_paths(env, {t: e for t, e in exes.items() if t not in before})

    def get_link_flags_for_target(
        self,
        target: Target,
        output_name: str,
        existing_flags: Sequence[FlagToken],
    ) -> list[FlagToken]:
        """A console program, as the ifx driver links one.

        A Fortran main program defines ``MAIN__``; ``main`` itself is in
        Intel's runtime library, so link.exe can't infer the subsystem from
        the objects and fails (LNK1561) without one. A subsystem the
        project chose wins.
        """
        flags = super().get_link_flags_for_target(target, output_name, existing_flags)
        if target.target_type == "program" and not any(
            isinstance(f, str) and f.upper().startswith("/SUBSYSTEM:")
            for f in existing_flags
        ):
            flags.append("/SUBSYSTEM:CONSOLE")
        return flags

    def _find_runtime_libdir(self, compiler: str) -> str | None:
        """ifx's runtime libraries sit in ``lib`` beside its ``bin``, in a
        oneAPI install and a conda environment alike."""
        libdir = Path(compiler).resolve().parent.parent / "lib"
        if (libdir / "libifcoremd.lib").is_file():
            return str(libdir)
        return None


# =============================================================================
# Registration
# =============================================================================

from pcons.tools.toolchain import toolchain_registry  # noqa: E402

# One "ifx" per host: its options and ABI follow the platform it runs on.
# Both list both platforms, so docs generated on either host show both.
if sys.platform == "win32":
    toolchain_registry.register(
        IfxMsvcToolchain,
        aliases=["ifx", "intel-fortran"],
        check_command="ifx",
        tool_classes=[IfxMsvcCompiler, MsvcLibrarian, MsvcLinker],
        category="fortran",
        platforms=["linux", "win32"],
        description="Intel Fortran compiler (ifx)",
        finder="find_fortran_toolchain()",
    )
else:
    toolchain_registry.register(
        IfxToolchain,
        aliases=["ifx", "intel-fortran"],
        check_command="ifx",
        tool_classes=[IfxCompiler, GccArchiver, IfxLinker],
        category="fortran",
        platforms=["linux", "win32"],
        description="Intel Fortran compiler (ifx)",
        finder="find_fortran_toolchain()",
    )
