# SPDX-License-Identifier: MIT
"""CUDA toolchain implementation.

Provides CUDA GPU compilation support using NVIDIA's nvcc compiler.
This toolchain is designed to be used alongside a C/C++ toolchain
(GCC, LLVM, or MSVC) for linking.

The CUDA toolchain handles:
- .cu file compilation via nvcc
- GPU architecture selection
- CUDA-specific variant settings (debug symbols, optimization)

Example:
    from pcons.toolchains import find_c_toolchain, find_cuda_toolchain

    cxx = find_c_toolchain()
    cuda = find_cuda_toolchain()

    env = project.Environment(toolchain=cxx)
    env.add_toolchain(cuda)  # Adds CUDA support

    # Or create CUDA-only environment
    cuda_env = project.Environment(toolchain=cuda)
"""

from __future__ import annotations

import logging
import shutil
from typing import TYPE_CHECKING, Any

from pcons.configure.platform import get_platform
from pcons.core.preset import ToolContribution
from pcons.core.subst import TargetPath
from pcons.tools.cuda import CudaCompiler
from pcons.tools.toolchain import BaseToolchain, SourceHandler, toolchain_registry

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    pass


class CudaToolchain(BaseToolchain):
    """CUDA toolchain for GPU development.

    This toolchain provides CUDA compilation support. It's typically used
    alongside a C/C++ toolchain which provides the linker.

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

    def __init__(self) -> None:
        super().__init__("cuda")

    def get_source_handler(self, suffix: str) -> SourceHandler | None:
        """Return handler for CUDA source files."""
        suffix_lower = suffix.lower()
        if suffix_lower == ".cu":
            # Use TargetPath for depfile - resolved to PathToken during resolution
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
                f"Supported CUDA variants: debug, release, relwithdebinfo, "
                f"profile, minsizerel."
            )
        flags = list(spec[0]) + list(kwargs.get("extra_flags", []))
        defines = list(spec[1]) + list(kwargs.get("extra_defines", []))
        return [ToolContribution("cuda", flags=tuple(flags), defines=tuple(defines))]


def find_cuda_toolchain() -> CudaToolchain | None:
    """Find CUDA installation and create toolchain.

    Checks for nvcc in PATH. Returns None if CUDA is not available.

    Returns:
        CudaToolchain if nvcc is found, None otherwise.

    Example:
        cuda = find_cuda_toolchain()
        if cuda:
            env.add_toolchain(cuda)
        else:
            print("CUDA not available, building without GPU support")
    """
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
