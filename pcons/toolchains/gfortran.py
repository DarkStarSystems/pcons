# SPDX-License-Identifier: MIT
"""GNU Fortran (gfortran) toolchain."""

from __future__ import annotations

from pcons.toolchains.fortran import (
    FortranCompiler,
    FortranLinker,
    FortranToolchain,
)
from pcons.toolchains.gcc import GccArchiver


class GfortranCompiler(FortranCompiler):
    """GNU Fortran compiler tool."""

    COMMANDS = ("gfortran",)
    MODDIR_FLAG = "-J"
    # Private entities, which submodules see but users of a module don't,
    # go here rather than into the .mod.
    ANCESTOR_FILE = "{name}.smod"


class GfortranLinker(FortranLinker):
    """Linker using gfortran as the driver, for Fortran runtime linkage."""

    COMMANDS = ("gfortran",)


class GfortranToolchain(FortranToolchain):
    """GNU Fortran toolchain: gfortran, ar, gfortran as linker."""

    TOOL_NAMES = ("fc", "ar", "link")
    ENV_COMPILER_FAMILY = "gfortran"
    WINDOWS_ABI = "mingw"

    COMPILER = GfortranCompiler
    ARCHIVER = GccArchiver
    LINKER = GfortranLinker
    RUNTIME_PROBE_FILE = "libgfortran.a"
    RUNTIME_LIBS = ("gfortran",)

    # Realized on `fc`; -Wpedantic omitted as it is noisy for legal Fortran.
    FEATURE_PRESETS: dict[str, dict[str, list[str]]] = {
        "warnings": {"compile_flags": ["-Wall", "-Wextra"]},
        "werror": {"compile_flags": ["-Werror"]},
        # gfortran is always real GCC (no Apple-clang shim concern), so
        # these realize statically.
        "openmp": {"compile_flags": ["-fopenmp"], "link_flags": ["-fopenmp"]},
        "coverage": {"compile_flags": ["--coverage"], "link_flags": ["--coverage"]},
        "fast-math": {"compile_flags": ["-ffast-math"], "link_flags": ["-ffast-math"]},
    }

    def __init__(self) -> None:
        super().__init__("gfortran")


# =============================================================================
# Registration
# =============================================================================

from pcons.tools.toolchain import toolchain_registry  # noqa: E402

toolchain_registry.register(
    GfortranToolchain,
    aliases=["gfortran"],
    check_command="gfortran",
    tool_classes=[GfortranCompiler, GccArchiver, GfortranLinker],
    category="fortran",
    platforms=["linux", "darwin", "win32"],
    description="GNU Fortran compiler (gfortran)",
    finder="find_fortran_toolchain()",
)
