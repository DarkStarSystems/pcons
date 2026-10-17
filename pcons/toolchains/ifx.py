# SPDX-License-Identifier: MIT
"""Intel Fortran (ifx) toolchains: GNU-style on Linux, MSVC-style on Windows.

ifx needs the oneAPI environment (``source /opt/intel/oneapi/setvars.sh``,
or ``setvars.bat`` on Windows) both to be found and for its programs to
find its shared runtime. On Windows that environment is also where ifx
finds its intrinsic modules (``INCLUDE``).
"""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pcons.core.subst import SourcePath, TargetPath
from pcons.toolchains._msvc_compat import MsvcCompatibleToolchain
from pcons.toolchains.fortran import (
    DEFAULT_MODDIR,
    FORTRAN_PREPROCESSED,
    FortranCompiler,
    FortranLinker,
    FortranToolchain,
    FortranToolchainBase,
)
from pcons.toolchains.gcc import GccArchiver
from pcons.toolchains.msvc import MsvcLibrarian, MsvcLinker, use_msvc_tool_paths
from pcons.tools.toolchain import LinkNeeds

if TYPE_CHECKING:
    from pcons.core.environment import Environment
    from pcons.core.preset import ToolContribution
    from pcons.core.target import Target
    from pcons.tools.toolchain import SourceHandler


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

    # No coverage: ifx accepts --coverage and does nothing with it.
    FEATURE_PRESETS: dict[str, dict[str, list[str]]] = {
        "warnings": {"compile_flags": ["-warn", "all"]},
        "werror": {"compile_flags": ["-warn", "errors"]},
        # Address only: given "address,undefined", ifx silently drops both.
        "sanitize": {
            "compile_flags": ["-fsanitize=address", "-fno-omit-frame-pointer"],
            "link_flags": ["-fsanitize=address"],
        },
        "profile": {"compile_flags": ["-pg", "-g"], "link_flags": ["-pg"]},
        "lto": {"compile_flags": ["-flto"], "link_flags": ["-flto"]},
        "hardened": {
            "compile_flags": ["-fstack-protector-strong", "-fPIE"],
            "link_flags": ["-pie", "-Wl,-z,relro,-z,now"],
        },
        "pthread": {"compile_flags": ["-pthread"], "link_flags": ["-pthread"]},
        "openmp": {"compile_flags": ["-qopenmp"], "link_flags": ["-qopenmp"]},
        "fast-math": {"compile_flags": ["-ffast-math"], "link_flags": ["-ffast-math"]},
    }

    def __init__(self) -> None:
        super().__init__("ifx")

    def link_needs(
        self,
        target: Target,
        languages: set[str],
        link_language: str,
        env: Environment,
    ) -> LinkNeeds:
        """Built with ``openmp``, ifx's objects need Intel's OpenMP runtime,
        which another driver's -fopenmp doesn't bring (g++'s is libgomp)."""
        needs = super().link_needs(target, languages, link_language, env)
        if (
            "fortran" in languages
            and link_language != "fortran"
            and env.feature_realized("openmp", self)
        ):
            libdirs = (self.runtime_libdir,) if self.runtime_libdir else ()
            needs = needs | LinkNeeds(libs=("iomp5",), libdirs=libdirs)
        return needs

    def apply_cross_preset(self, env: Environment, preset: Any) -> None:
        """Raise for a preset with a triple: ifx builds for its host only."""
        triple = str(getattr(preset, "triple", None) or "")
        # A wasm triple gets UnixToolchain's "use the dedicated toolchain".
        if triple and not triple.startswith("wasm32"):
            name = getattr(preset, "name", preset)
            raise ValueError(
                f"Cross preset '{name}' targets triple '{triple}', but {self.name} "
                f"has no cross targets: it builds for the host it runs on."
            )
        super().apply_cross_preset(env, preset)

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
        def compile_command(*extra: str) -> list[object]:
            return [
                "$fc.cmd",
                "$fc.flags",
                *extra,
                "${prefix(fc.iprefix, fc.includes)}",
                "${prefix(fc.dprefix, fc.defines)}",
                f"{self.MODDIR_FLAG}$fc.moddir",
                "/I",
                "$fc.moddir",
                "/c",
                TargetPath(prefix="/Fo"),
                SourcePath(),
            ]

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
            "objcmd": compile_command(),
            # Windows file names don't keep case, so ifx can't tell .F90 from
            # .f90 there and preprocesses only when asked.
            "fppcmd": compile_command("/fpp"),
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

    # No lto: the objects are bitcode only ifx's own lld-link reads. No
    # pthread, as with MSVC, and no coverage (/Qcov-gen is gone).
    FEATURE_PRESETS: dict[str, dict[str, list[str]]] = {
        "warnings": {"compile_flags": ["/warn:all"]},
        "werror": {"compile_flags": ["/warn:errors"]},
        # Without /INFERASANLIBS, link.exe can't resolve __asan_init.
        "sanitize": {
            "compile_flags": ["/fsanitize=address"],
            "link_flags": ["/INFERASANLIBS"],
        },
        "profile": MsvcCompatibleToolchain.FEATURE_PRESETS["profile"],
        "hardened": MsvcCompatibleToolchain.FEATURE_PRESETS["hardened"],
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

    def _arch_contributions(self, arch: str) -> list[ToolContribution]:
        """/MACHINE:X64, the only machine ifx builds for."""
        if self.MSVC_MACHINE_MAP.get(arch.lower()) != "X64":
            raise ValueError(
                f"{self.name} builds x64 only, so it can't target '{arch}'."
            )
        return super()._arch_contributions(arch)

    def get_source_handler(self, suffix: str) -> SourceHandler | None:
        handler = super().get_source_handler(suffix)
        if handler is not None and suffix in FORTRAN_PREPROCESSED:
            return dataclasses.replace(handler, command_var="fppcmd")
        return handler

    def setup(self, env: Environment) -> None:
        """Set up ifx's tools, with link.exe and lib.exe by full path when
        PATH would find another program first (Git's link.exe). Only tools
        this toolchain added: an existing linker (clang-cl's lld-link,
        say) stays."""
        before = set(env.tool_names())
        super().setup(env)
        exes = {"link": "link.exe", "lib": "lib.exe"}
        use_msvc_tool_paths(env, {t: e for t, e in exes.items() if t not in before})

    def link_needs(
        self,
        target: Target,
        languages: set[str],
        link_language: str,
        env: Environment,
    ) -> LinkNeeds:
        """A program with ifx objects is a console program, as the ifx
        driver links one: a Fortran main program defines ``MAIN__``, and
        ``main`` itself is in Intel's runtime, so the linker can't infer
        the subsystem from the objects (LNK1561). Which object holds the
        entry point isn't known when the build is described, so every
        such program is a console one, as CMake makes every executable."""
        needs = super().link_needs(target, languages, link_language, env)
        if target.target_type == "program" and "fortran" in languages:
            needs = needs | LinkNeeds(console=True)
        return needs

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
