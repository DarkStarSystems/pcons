# SPDX-License-Identifier: MIT
"""GNU Fortran (gfortran) toolchain."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pcons.toolchains.fortran import (
    FortranCompiler,
    FortranLinker,
    FortranToolchain,
)
from pcons.toolchains.gcc import GccArchiver
from pcons.toolchains.gnu_common import require_cross_binaries
from pcons.toolchains.unix import UnixToolchain
from pcons.tools.toolchain import LinkNeeds

if TYPE_CHECKING:
    from pcons.core.environment import Environment
    from pcons.core.target import Target

_GCC_HARDENED = UnixToolchain.FEATURE_PRESETS["hardened"]


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

    # GCC's, realized on `fc`. -Wpedantic is left out as noisy for legal
    # Fortran, and _FORTIFY_SOURCE does nothing for it.
    FEATURE_PRESETS: dict[str, dict[str, list[str]]] = {
        **UnixToolchain.FEATURE_PRESETS,
        "warnings": {"compile_flags": ["-Wall", "-Wextra"]},
        "hardened": {
            **_GCC_HARDENED,
            "compile_flags": [
                f
                for f in _GCC_HARDENED["compile_flags"]
                if not f.startswith("-D_FORTIFY_SOURCE")
            ],
        },
        # gfortran is always real GCC (no Apple-clang shim concern).
        "openmp": {"compile_flags": ["-fopenmp"], "link_flags": ["-fopenmp"]},
    }

    def __init__(self) -> None:
        super().__init__("gfortran")

    def link_needs(
        self,
        target: Target,
        languages: set[str],
        link_language: str,
        env: Environment,
    ) -> LinkNeeds:
        """Built with ``coverage``, gfortran's objects need libgcov, which
        a non-GCC driver (clang++) doesn't add for its own --coverage."""
        needs = super().link_needs(target, languages, link_language, env)
        if (
            "fortran" in languages
            and link_language != "fortran"
            and env.feature_realized("coverage", self)
        ):
            needs = needs | LinkNeeds(libs=("gcov",))
        return needs

    def apply_cross_preset(self, env: Environment, preset: Any) -> None:
        """Apply a cross preset, requiring a cross gfortran for a triple."""
        require_cross_binaries(
            preset, "gfortran", ("fc",), "fc={triple}-gfortran", "flang"
        )
        super().apply_cross_preset(env, preset)


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
