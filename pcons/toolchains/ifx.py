# SPDX-License-Identifier: MIT
"""Intel Fortran (ifx) toolchain, for Linux.

ifx needs the oneAPI environment (``source /opt/intel/oneapi/setvars.sh``)
both to be found and for its programs to find its shared runtime.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pcons.toolchains.fortran import (
    FortranCompiler,
    FortranLinker,
    FortranToolchain,
)
from pcons.toolchains.gcc import GccArchiver

if TYPE_CHECKING:
    from pcons.core.environment import Environment


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


# =============================================================================
# Registration
# =============================================================================

from pcons.tools.toolchain import toolchain_registry  # noqa: E402

toolchain_registry.register(
    IfxToolchain,
    aliases=["ifx", "intel-fortran"],
    check_command="ifx",
    tool_classes=[IfxCompiler, GccArchiver, IfxLinker],
    category="fortran",
    platforms=["linux"],
    description="Intel Fortran compiler (ifx)",
    finder="find_fortran_toolchain()",
)
