# SPDX-License-Identifier: MIT
"""LLVM Flang toolchain (the ``flang`` driver, LLVM 21 or newer)."""

from __future__ import annotations

import functools
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pcons.core.preset import Preset, ToolContribution
from pcons.toolchains.fortran import (
    FortranCompiler,
    FortranLinker,
    FortranToolchain,
)
from pcons.toolchains.llvm import LlvmArchiver
from pcons.toolchains.unix import UnixToolchain, target_triple

if TYPE_CHECKING:
    from pcons.core.environment import Environment

logger = logging.getLogger(__name__)


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
            resource_dir = subprocess.check_output(
                [compiler, "-print-resource-dir"],
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=30,
            ).strip()
        except (OSError, subprocess.SubprocessError):
            return None
        lib = Path(resource_dir) / "lib"
        triple = target_triple(compiler)
        by_triple = [lib / triple] if triple else []
        for candidate in (*by_triple, lib / "darwin", lib / "linux"):
            if any(candidate.glob("*flang_rt.runtime.*")):
                return str(candidate)
        return None


@functools.cache
def intrinsic_modules_problem(flang: str) -> str | None:
    """Why *flang* can't compile a ``USE iso_c_binding``, or None if it can.

    A driver can be installed without its intrinsic modules: Visual Studio's
    bundled LLVM ships flang.exe that way. Such a flang fails on nearly any
    real program, so it doesn't count as available.
    """
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "probe.f90"
        source.write_text("program probe\n  use iso_c_binding\nend program\n")
        try:
            result = subprocess.run(
                [flang, "-fsyntax-only", str(source)],
                capture_output=True,
                text=True,
                timeout=60,
                cwd=tmp,
            )
        except (OSError, subprocess.SubprocessError) as e:
            return str(e)
    if result.returncode == 0:
        return None
    said = (result.stderr or result.stdout).strip().splitlines()
    if not said:
        return f"exit code {result.returncode}"
    # flang leads with "error: Semantic errors in <file>"; the line that
    # says what's wrong names the module. Its "<file>:2:7: " is noise.
    line = next((s for s in said if "iso_c_binding" in s), said[0])
    return line[line.find("error: ") :] if "error: " in line else line


def _flang_is_available() -> bool:
    """A flang on PATH that can compile programs using intrinsic modules."""
    flang = shutil.which("flang")
    if flang is None:
        return False
    problem = intrinsic_modules_problem(flang)
    if problem is not None:
        logger.warning(
            "Not using %s: it can't find its intrinsic modules (%s).", flang, problem
        )
        return False
    return True


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
    is_available=_flang_is_available,
)
