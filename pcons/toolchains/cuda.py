# SPDX-License-Identifier: MIT
"""CUDA toolchain: .cu compilation via NVIDIA's nvcc.

Typically added alongside a C/C++ toolchain (GCC, LLVM, or MSVC), which
provides the linker: env.add_toolchain(find_cuda_toolchain()).
"""

from __future__ import annotations

import functools
import logging
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from pcons.configure.platform import Platform, get_platform
from pcons.core.preset import Preset, ToolContribution
from pcons.core.subst import TargetPath
from pcons.toolchains._msvc_compat import MsvcCompatibleToolchain, msvc_crt
from pcons.toolchains.gnu_common import GNU_COMPILE_PATH_FLAGS, needs_pic
from pcons.tools.cuda import CudaCompiler
from pcons.tools.toolchain import (
    BaseToolchain,
    LinkNeeds,
    SourceHandler,
    toolchain_registry,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from pcons.configure.platform import Platform
    from pcons.core.environment import Environment
    from pcons.core.target import Target


@functools.cache
def _runtime_libdir(nvcc: str, library_file: str) -> str | None:
    """The directory of the CUDA toolkit *nvcc* belongs to that holds
    *library_file*: ``lib/x64`` on Windows, ``lib64`` on Linux, ``lib`` in
    a conda environment."""
    found = shutil.which(nvcc)
    if found is None:
        return None
    root = Path(found).resolve().parent.parent
    for libdir in ("lib/x64", "lib64", "lib"):
        if (root / libdir / library_file).is_file():
            return str(root / libdir)
    return None


class CudaToolchain(BaseToolchain):
    """CUDA toolchain, typically added alongside a C/C++ toolchain for linking.

    GPU Architectures:
        - sm_50: Maxwell
        - sm_60: Pascal
        - sm_70: Volta
        - sm_75: Turing (default)
        - sm_80: Ampere
        - sm_86: Ampere (consumer)
        - sm_89: Ada Lovelace
        - sm_90: Hopper

    Example:
        cuda = find_cuda_toolchain()
        env = project.Environment(toolchain=cxx_toolchain)
        env.add_toolchain(cuda)
        env.cuda.arch = "sm_86"  # Target specific GPU
    """

    TOOL_NAMES = ("cuda",)
    COMPILE_PATH_FLAGS = GNU_COMPILE_PATH_FLAGS

    # Compile flags only: the host toolchain realizes the link side. nvcc
    # rejects bare host flags, so each goes through as one -Xcompiler=
    # token, with no commas: -Xcompiler splits on them. These are for a
    # GCC or Clang host. No lto: device LTO needs -dlink, and host -flto
    # breaks on nvcc's fatbinData. sanitize, coverage and openmp reach
    # host code only.
    FEATURE_PRESETS: dict[str, dict[str, list[str]]] = {
        # -Wpedantic floods nvcc's generated code.
        "warnings": {"compile_flags": ["-Xcompiler=-Wall", "-Xcompiler=-Wextra"]},
        "werror": {"compile_flags": ["-Werror=all-warnings"]},
        "sanitize": {
            "compile_flags": [
                "-Xcompiler=-fsanitize=address",
                "-Xcompiler=-fsanitize=undefined",
                "-Xcompiler=-fno-omit-frame-pointer",
            ]
        },
        "profile": {"compile_flags": ["-pg"]},
        "hardened": {
            "compile_flags": [
                "-Xcompiler=-fstack-protector-strong",
                "-D_FORTIFY_SOURCE=2",
                "-Xcompiler=-fPIE",
            ]
        },
        "pthread": {"compile_flags": ["-Xcompiler=-pthread"]},
        "coverage": {"compile_flags": ["-Xcompiler=--coverage"]},
        "fast-math": {"compile_flags": ["-use_fast_math", "-Xcompiler=-ffast-math"]},
        "openmp": {"compile_flags": ["-Xcompiler=-fopenmp"]},
    }

    # With cl.exe as the host compiler (Windows): MSVC's compile flags, as
    # MsvcCompatibleToolchain has them. nvcc's -Werror=all-warnings already
    # passes /WX to cl. cl's ASan objects name their runtime, so the link
    # needs nothing. profile is MSVC's link-only /PROFILE, the host
    # linker's to realize; no lto, pthread or coverage, as with MSVC.
    MSVC_HOST_FEATURE_PRESETS: dict[str, dict[str, list[str]]] = {
        "warnings": {"compile_flags": ["-Xcompiler=/W4"]},
        "werror": {"compile_flags": ["-Werror=all-warnings"]},
        "sanitize": {"compile_flags": ["-Xcompiler=/fsanitize=address"]},
        "profile": {"compile_flags": []},
        "hardened": {"compile_flags": ["-Xcompiler=/GS", "-Xcompiler=/guard:cf"]},
        "fast-math": {"compile_flags": ["-use_fast_math", "-Xcompiler=/fp:fast"]},
        "openmp": {"compile_flags": ["-Xcompiler=/openmp"]},
    }

    #: The libraries each ``env.cuda.runtime`` links, as CMake's
    #: CUDA_RUNTIME_LIBRARY has them. ``static`` is nvcc's own default.
    CUDA_RUNTIMES: ClassVar[dict[str, tuple[str, ...]]] = {
        "static": ("cudart_static",),
        "shared": ("cudart",),
        "none": (),
    }

    def __init__(self) -> None:
        super().__init__("cuda")

    def link_needs(
        self,
        target: Target,
        languages: set[str],
        link_language: str,
        env: Environment,
    ) -> LinkNeeds:
        """CUDA objects need the CUDA runtime ``env.cuda.runtime`` picks,
        which the host compiler's driver doesn't know of, and its
        directory. On Linux, the static runtime needs libdl, librt and
        libpthread. On Windows, cudart_static.lib names the static release
        C runtime (LIBCMT), which a program with any other leaves out."""
        if "cuda" not in languages or not env.has_tool("cuda"):
            return LinkNeeds()
        runtime = env.cuda.get("runtime")
        libs = self.CUDA_RUNTIMES.get(runtime)
        if libs is None:
            raise ValueError(
                f"env.cuda.runtime is {runtime!r}; it takes "
                f"{', '.join(repr(r) for r in self.CUDA_RUNTIMES)}."
            )
        if not libs:
            return LinkNeeds()
        platform = env.target
        # Windows links the shared runtime through its import library.
        static = runtime == "static" or platform.is_windows
        suffix = platform.static_lib_suffix if static else platform.shared_lib_suffix
        libfile = f"{platform.static_lib_prefix}{libs[0]}{suffix}"
        libdir = _runtime_libdir(str(env.cuda.cmd), libfile)
        needs = LinkNeeds(libs=libs, libdirs=(libdir,) if libdir else ())
        if runtime != "static":
            return needs
        if platform.is_windows:
            if msvc_crt(env.runtime_linkage, env.variant) == "static":
                return needs
            return needs | LinkNeeds(excluded_default_libs=("LIBCMT",))
        if platform.is_linux:
            return needs | LinkNeeds(libs=("dl", "rt", "pthread"))
        return needs

    def _feature_preset_tools(self) -> tuple[str, ...]:
        return ("cuda",)

    def _runtime_contributions(
        self, linkage: str, variant: str, target: Platform
    ) -> list[ToolContribution] | None:
        """On Windows, the host compile's C runtime is cl's, through
        -Xcompiler. Elsewhere the C/C++ toolchain's link has the runtimes."""
        if not target.is_windows:
            return []
        flag = MsvcCompatibleToolchain.MSVC_CRT_FLAGS[msvc_crt(linkage, variant)]
        return [ToolContribution("cuda", flags=(f"-Xcompiler={flag}",))]

    def make_feature_preset(
        self, name: str, target: Platform | None = None
    ) -> Preset | None:
        """FEATURE_PRESETS for a GCC or Clang host; on Windows, where the
        host compiler is cl.exe, MSVC_HOST_FEATURE_PRESETS."""
        if not get_platform().is_windows:
            return super().make_feature_preset(name, target)
        spec = self.MSVC_HOST_FEATURE_PRESETS.get(name)
        if spec is None:
            return None
        flags = tuple(spec["compile_flags"])
        contribs = (ToolContribution("cuda", flags=flags),) if flags else ()
        return Preset(name=name, category="feature", contributions=contribs)

    def get_compile_flags_for_target_type(
        self,
        target_type: str,
        env: Environment | None = None,
        *,
        pic: bool | None = None,
    ) -> list[str]:
        """``-fPIC`` for the host compiler, where GCC and Clang need it."""
        platform = env.target if env is not None else get_platform()
        return ["-Xcompiler=-fPIC"] if needs_pic(target_type, platform, pic) else []

    def make_target_preset(self, cross: Any) -> Preset:
        """Raise for a preset with a triple; one without applies as usual."""
        if getattr(cross, "triple", None):
            name = getattr(cross, "name", cross)
            raise ValueError(
                f"Cross preset '{name}' targets triple '{cross.triple}', but "
                f"CUDA cross targets aren't supported."
            )
        return super().make_target_preset(cross)

    #: Names one CPU goes by.
    _ARCH_ALIASES: ClassVar[dict[str, str]] = {
        "x64": "x86_64",
        "amd64": "x86_64",
        "aarch64": "arm64",
    }

    def make_arch_preset(self, arch: str) -> Preset | None:
        """The host's CPU needs nothing (nvcc builds for it); another one
        needs a cross nvcc, so it's an error."""

        def canonical(name: str) -> str:
            return self._ARCH_ALIASES.get(name.lower(), name.lower())

        host = get_platform().arch
        if canonical(arch) != canonical(host):
            raise ValueError(
                f"{self.name} can't build for target arch '{arch}': nvcc builds "
                f"for the host's ({host})."
            )
        return Preset(name=arch, category="arch", arch=arch)

    def get_source_handler(self, suffix: str) -> SourceHandler | None:
        """Return handler for CUDA source files."""
        suffix_lower = suffix.lower()
        if suffix_lower == ".cu":
            return SourceHandler(
                "cuda",
                "cuda",
                self.get_object_suffix(),
                TargetPath(suffix=".d"),
                "gcc",
            )
        return None

    def get_object_suffix(self) -> str:
        """CUDA object suffix matches the host platform (.obj on Windows, .o elsewhere).

        nvcc is paired with a host C/C++ toolchain (MSVC on Windows, GCC/Clang
        elsewhere), so its objects must match that toolchain's convention.
        """
        return get_platform().object_suffix

    # CUDA variant flags per build type (compile_flags, defines).
    # -G enables device debugging; -lineinfo gives profiler line info; nvcc
    # has no -Os, so minsizerel uses -O1.
    CUDA_VARIANTS: dict[str, tuple[list[str], list[str]]] = {
        "debug": (["-g", "-G", "-O0"], ["DEBUG", "_DEBUG"]),
        "release": (["-O3"], ["NDEBUG"]),
        # nvcc's release is already its highest level; the name exists so a
        # script can set it on every toolchain in the environment.
        "release-fastest": (["-O3"], ["NDEBUG"]),
        "relwithdebinfo": (["-O2", "-lineinfo"], ["NDEBUG"]),
        "profile": (["-O3", "-lineinfo"], ["NDEBUG"]),
        "minsizerel": (["-O1"], ["NDEBUG"]),
    }

    def _variant_contributions(
        self, variant: str, **kwargs: Any
    ) -> list[ToolContribution]:
        """CUDA variant flags applied to the nvcc compiler."""
        spec = self.CUDA_VARIANTS.get(variant.lower())
        if spec is None:
            raise ValueError(
                f"Unknown variant '{variant}'. "
                f"Supported CUDA variants: debug, release, release-fastest, "
                f"relwithdebinfo, "
                f"profile, minsizerel."
            )
        flags = list(spec[0]) + list(kwargs.get("extra_flags", []))
        defines = list(spec[1]) + list(kwargs.get("extra_defines", []))
        return [ToolContribution("cuda", flags=tuple(flags), defines=tuple(defines))]


def find_cuda_toolchain() -> CudaToolchain | None:
    """Return a CudaToolchain if nvcc is in PATH, else None."""
    if shutil.which("nvcc"):
        toolchain = CudaToolchain()
        toolchain._tools = {"cuda": CudaCompiler()}
        return toolchain
    return None


# =============================================================================
# Registration
# =============================================================================

toolchain_registry.register(
    CudaToolchain,
    aliases=["cuda", "nvcc"],
    check_command="nvcc",
    tool_classes=[CudaCompiler],
    category="cuda",  # Separate category since it's often used alongside C
    platforms=["linux", "win32"],
    description="NVIDIA CUDA compiler (nvcc)",
    finder="find_cuda_toolchain()",
)


toolchain_registry.register_finder(
    ["cuda"],
    find_cuda_toolchain,
    description="Auto-detect the NVIDIA CUDA toolchain (nvcc)",
)
