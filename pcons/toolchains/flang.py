# SPDX-License-Identifier: MIT
"""LLVM Flang toolchain (the ``flang`` driver, LLVM 21 or newer)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pcons.core.preset import Preset, ToolContribution
from pcons.toolchains.fortran import (
    FortranCompiler,
    FortranLinker,
    FortranToolchain,
)
from pcons.toolchains.llvm import LlvmArchiver
from pcons.toolchains.unix import UnixToolchain

if TYPE_CHECKING:
    from pcons.core.environment import Environment


class FlangCompiler(FortranCompiler):
    """LLVM Flang compiler tool."""

    COMMANDS = ("flang",)
    MODDIR_FLAG = "-module-dir"
    SUBMODULE_FILE = "{ancestor}-{name}.mod"


class FlangLinker(FortranLinker):
    """Linker using flang as the driver, for Fortran runtime linkage."""

    COMMANDS = ("flang",)


class FlangToolchain(FortranToolchain):
    """LLVM Flang toolchain: flang, llvm-ar (else ar), flang as linker."""

    TOOL_NAMES = ("fc", "ar", "link")
    ENV_COMPILER_FAMILY = "flang"
    # LLVM's own Windows builds target MSVC; MSYS2's clang64 one MinGW.
    WINDOWS_ABI = "msvc"

    def _abi_probe_compiler(self) -> str | None:
        """$FC, else flang on PATH: the compiler that will build."""
        return self.compiler_path

    COMPILER = FlangCompiler
    ARCHIVER = LlvmArchiver
    LINKER = FlangLinker
    RUNTIME_LIBS = ("flang_rt.runtime",)

    # Flang ignores -Wextra (with a warning) and has no --coverage.
    FEATURE_PRESETS: dict[str, dict[str, list[str]]] = {
        "warnings": {"compile_flags": ["-Wall"]},
        "werror": {"compile_flags": ["-Werror"]},
        "openmp": {"compile_flags": ["-fopenmp"], "link_flags": ["-fopenmp"]},
        "fast-math": {"compile_flags": ["-ffast-math"], "link_flags": ["-ffast-math"]},
    }

    # Flang has no -Os, so minsizerel optimizes as release does.
    UNIX_VARIANTS = {
        **UnixToolchain.UNIX_VARIANTS,
        "minsizerel": (["-O2"], ["NDEBUG"]),
    }

    def __init__(self) -> None:
        super().__init__("flang")

    # On Windows, the C runtime each variant links: the DLL one, as
    # pcons' MSVC and clang-cl builds use (/MD, /MDd), so Fortran and C/C++
    # objects agree. flang's own default is the static one.
    WINDOWS_CRT = {"debug": "dll_dbg"}
    WINDOWS_DEFAULT_CRT = "dll"

    @staticmethod
    def _crt_flag(crt: str) -> ToolContribution:
        return ToolContribution("fc", flags=(f"-fms-runtime-lib={crt}",))

    def setup_presets(self, env: Environment) -> list[Preset]:
        """The DLL C runtime from the start, targeting MSVC."""
        presets = super().setup_presets(env)
        if self.abi != "msvc":
            return presets
        crt = Preset(
            name="default",
            category="variant",
            exclusive_group="build_variant",
            contributions=(self._crt_flag(self.WINDOWS_DEFAULT_CRT),),
        )
        return [*presets, crt]

    def _variant_contributions(
        self, variant: str, **kwargs: Any
    ) -> list[ToolContribution]:
        contribs = super()._variant_contributions(variant, **kwargs)
        if self.abi == "msvc":
            crt = self.WINDOWS_CRT.get(variant.lower(), self.WINDOWS_DEFAULT_CRT)
            contribs.append(self._crt_flag(crt))
        return contribs

    @property
    def objects_name_runtimes(self) -> bool:
        """Targeting MSVC, flang's objects name their runtime
        (``/DEFAULTLIB:flang_rt.runtime.static.lib``); targeting MinGW, a
        link names it (-lflang_rt.runtime) as on Linux."""
        return self.abi == "msvc"

    def _find_runtime_libdir(self, compiler: str) -> str | None:
        """The directory holding flang_rt.runtime.

        It is a per-target directory under flang's resource directory:
        ``lib/<triple>`` on Linux and Windows, ``lib/darwin`` on macOS.
        """
        try:
            resource_dir, triple = (
                subprocess.check_output(
                    [compiler, flag], stderr=subprocess.DEVNULL, text=True
                ).strip()
                for flag in ("-print-resource-dir", "-print-target-triple")
            )
        except (subprocess.CalledProcessError, OSError):
            return None
        lib = Path(resource_dir) / "lib"
        for candidate in (lib / triple, lib / "darwin", lib / "linux"):
            if any(candidate.glob("*flang_rt.runtime.*")):
                return str(candidate)
        return None


# =============================================================================
# Registration
# =============================================================================

from pcons.tools.toolchain import toolchain_registry  # noqa: E402

toolchain_registry.register(
    FlangToolchain,
    aliases=["flang"],
    check_command="flang",
    tool_classes=[FlangCompiler, LlvmArchiver, FlangLinker],
    category="fortran",
    platforms=["linux", "darwin", "win32"],
    description="LLVM Flang Fortran compiler (flang)",
    finder="find_fortran_toolchain()",
)
