# SPDX-License-Identifier: MIT
"""CUDA toolchain: .cu compilation via NVIDIA's nvcc.

Typically added alongside a C/C++ toolchain (GCC, LLVM, or MSVC), which
provides the linker: env.add_toolchain(find_cuda_toolchain()).
"""

from __future__ import annotations

import logging
import shutil
from typing import TYPE_CHECKING, Any

from pcons.configure.platform import get_platform
from pcons.core.preset import Preset, ToolContribution
from pcons.core.subst import TargetPath
from pcons.toolchains.gnu_common import needs_pic
from pcons.tools.cuda import CudaCompiler
from pcons.tools.toolchain import BaseToolchain, SourceHandler, toolchain_registry

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from pcons.core.environment import Environment


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

    def __init__(self) -> None:
        super().__init__("cuda")

    def _feature_preset_tools(self) -> tuple[str, ...]:
        return ("cuda",)

    def make_feature_preset(self, name: str) -> Preset | None:
        """The table's presets, which are GCC and Clang host flags. On
        Windows nvcc's host compiler is cl.exe, which none of them suit."""
        if get_platform().is_windows:
            return None
        return super().make_feature_preset(name)

    def get_compile_flags_for_target_type(
        self, target_type: str, env: Environment | None = None
    ) -> list[str]:
        """``-fPIC`` for the host compiler, where GCC and Clang need it."""
        platform = env.target if env is not None else get_platform()
        return ["-Xcompiler=-fPIC"] if needs_pic(target_type, platform) else []

    def apply_cross_preset(self, env: Environment, preset: Any) -> None:
        """Raise for a preset with a triple; one without applies as usual."""
        if getattr(preset, "triple", None):
            name = getattr(preset, "name", preset)
            raise ValueError(
                f"Cross preset '{name}' targets triple '{preset.triple}', but "
                f"CUDA cross targets aren't supported."
            )
        super().apply_cross_preset(env, preset)

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

    def _configure_tools(self, config: object) -> bool:
        from pcons.configure.config import Configure

        if not isinstance(config, Configure):
            return False

        cuda = CudaCompiler()
        if cuda.configure(config) is None:
            return False

        self._tools = {"cuda": cuda}
        return True

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
        # Quick setup without full configure
        toolchain._tools = {"cuda": CudaCompiler()}
        toolchain._configured = True
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
