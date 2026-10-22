# SPDX-License-Identifier: MIT
"""LLVM Flang toolchain (the ``flang`` driver, LLVM 21 or newer)."""

from __future__ import annotations

import functools
import logging
import shutil
import subprocess
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from pcons.core.preset import Preset, ToolContribution
from pcons.toolchains.fortran import (
    FortranCompiler,
    FortranLinker,
    FortranToolchain,
)
from pcons.toolchains.gnu_common import GNU_COLOR_FLAGS
from pcons.toolchains.llvm import LlvmArchiver
from pcons.toolchains.unix import UnixToolchain, target_triple

if TYPE_CHECKING:
    from pcons.configure.platform import Platform

logger = logging.getLogger(__name__)


class FlangCompiler(FortranCompiler):
    """LLVM Flang compiler tool."""

    COMMANDS = ("flang",)
    MODDIR_FLAG = "-module-dir"
    # Unlike clang, flang takes no -fansi-escape-codes, so on Windows it
    # colors through the console API, which a build's pipe ignores.
    COLOR_FLAGS = GNU_COLOR_FLAGS
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

    # Flang has no sanitizers, gprof, stack protector or coverage. Its -Wall
    # does nothing but warn that it's ignored, so warnings is a deliberate
    # no-op. -pthread matters only to the link: the compile calls it unused.
    # Targeting MSVC, make_feature_preset adjusts profile, lto and pthread.
    FEATURE_PRESETS: dict[str, dict[str, list[str]]] = {
        "warnings": {"compile_flags": []},
        "werror": {"compile_flags": ["-Werror"]},
        "lto": {"compile_flags": ["-flto"], "link_flags": ["-flto"]},
        "pthread": {"link_flags": ["-pthread"]},
        "openmp": {"compile_flags": ["-fopenmp"], "link_flags": ["-fopenmp"]},
        "fast-math": {"compile_flags": ["-ffast-math"], "link_flags": ["-ffast-math"]},
    }

    # A clang-family driver: --target= retargets it.
    IS_CLANG_DRIVER = True

    # Flang has no -Os, so minsizerel optimizes as release does.
    UNIX_VARIANTS = {
        **UnixToolchain.UNIX_VARIANTS,
        "minsizerel": (["-O2"], ["NDEBUG"]),
    }

    def __init__(self) -> None:
        super().__init__("flang")

    def make_feature_preset(
        self, name: str, target: Platform | None = None
    ) -> Preset | None:
        """Targeting MSVC, profile and pthread are as MSVC's presets have
        them, and LTO links with lld, the linker that reads flang's bitcode."""
        if self.abi != "msvc":
            return super().make_feature_preset(name, target)
        if name == "pthread":
            return None
        if name == "profile":
            link = ToolContribution("link", flags=("-Wl,/PROFILE",))
            return Preset(name=name, category="feature", contributions=(link,))
        preset = super().make_feature_preset(name, target)
        if preset is not None and name == "lto":
            lld = ToolContribution("link", flags=("-fuse-ld=lld",))
            preset = replace(preset, contributions=(*preset.contributions, lld))
        return preset

    STATIC_RUNTIME_LINK_FLAGS = ("-static-libflangrt",)

    def _runtime_contributions(
        self, linkage: str, variant: str, target: Platform
    ) -> list[ToolContribution] | None:
        """flang links its runtime static only where it has the archive:
        Homebrew's flang has only the shared one."""
        contribs = super()._runtime_contributions(linkage, variant, target)
        if (
            linkage == "static"
            and not self._targets_msvc_abi(target)
            and not self.has_static_runtime
        ):
            return None
        return contribs

    @functools.cached_property
    def has_static_runtime(self) -> bool:
        """Whether flang's runtime directory has the static flang-rt."""
        libdir = self.runtime_libdir
        return libdir is not None and (Path(libdir) / "libflang_rt.runtime.a").is_file()

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
