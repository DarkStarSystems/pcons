# SPDX-License-Identifier: MIT
"""LLVM Flang toolchain (the ``flang`` driver, LLVM 21 or newer)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from pcons.toolchains.fortran import (
    FortranCompiler,
    FortranLinker,
    FortranToolchain,
)
from pcons.toolchains.llvm import LlvmArchiver
from pcons.toolchains.unix import UnixToolchain


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

    def _find_runtime_libdir(self, compiler: str) -> str | None:
        """The directory holding libflang_rt.runtime.

        It is a per-target directory under flang's resource directory:
        ``lib/<triple>`` on Linux, ``lib/darwin`` on macOS.
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
            if any(candidate.glob("libflang_rt.runtime.*")):
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
    platforms=["linux", "darwin"],
    description="LLVM Flang Fortran compiler (flang)",
    finder="find_fortran_toolchain()",
)
