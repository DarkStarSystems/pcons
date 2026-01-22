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

from pcons.core.subst import TargetPath
from pcons.tools.cuda import CudaCompiler
from pcons.tools.toolchain import BaseToolchain, SourceHandler, toolchain_registry

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from pcons.core.environment import Environment


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

    def __init__(self) -> None:
        super().__init__("cuda")

    def get_source_handler(self, suffix: str) -> SourceHandler | None:
        """Return handler for CUDA source files."""
        suffix_lower = suffix.lower()
        if suffix_lower == ".cu":
            # Use TargetPath for depfile - resolved to PathToken during resolution
            return SourceHandler("cuda", "cuda", ".o", TargetPath(suffix=".d"), "gcc")
        return None

    def get_object_suffix(self) -> str:
        """CUDA produces standard object files."""
        return ".o"

    def _configure_tools(self, config: object) -> bool:
        from pcons.configure.config import Configure

        if not isinstance(config, Configure):
            return False

        cuda = CudaCompiler()
        if cuda.configure(config) is None:
            return False

        self._tools = {"cuda": cuda}
        return True

    def apply_variant(self, env: Environment, variant: str, **kwargs: Any) -> None:
        """Apply build variant to CUDA compiler.

        CUDA-specific variant settings:
        - debug: Device debug symbols (-G), host debug (-g)
        - release: Optimization (-O3), no device debug
        - profile: Line info for profilers (-lineinfo)

        Args:
            env: Environment to modify.
            variant: Variant name (debug, release, profile, etc.).
            **kwargs: Optional extra_flags and extra_defines.
        """
        super().apply_variant(env, variant, **kwargs)

        compile_flags: list[str] = []
        defines: list[str] = []

        variant_lower = variant.lower()
        if variant_lower == "debug":
            # -G enables device debugging (slower, larger code)
            # -g enables host code debugging
            compile_flags = ["-g", "-G", "-O0"]
            defines = ["DEBUG", "_DEBUG"]
        elif variant_lower == "release":
            compile_flags = ["-O3"]
            defines = ["NDEBUG"]
        elif variant_lower == "relwithdebinfo":
            # Line info for profiling without full debug
            compile_flags = ["-O2", "-lineinfo"]
            defines = ["NDEBUG"]
        elif variant_lower == "profile":
            # Optimized with line info for profilers like Nsight
            compile_flags = ["-O3", "-lineinfo"]
            defines = ["NDEBUG"]
        elif variant_lower == "minsizerel":
            compile_flags = ["-O1"]  # nvcc doesn't have -Os
            defines = ["NDEBUG"]
        else:
            logger.warning("Unknown variant '%s', no flags applied", variant)

        # Add extra flags/defines from kwargs
        extra_flags = kwargs.get("extra_flags", [])
        extra_defines = kwargs.get("extra_defines", [])
        compile_flags.extend(extra_flags)
        defines.extend(extra_defines)

        # Apply to CUDA compiler
        if env.has_tool("cuda"):
            tool = env.cuda
            if hasattr(tool, "flags") and isinstance(tool.flags, list):
                tool.flags.extend(compile_flags)
            if hasattr(tool, "defines") and isinstance(tool.defines, list):
                tool.defines.extend(defines)

    def _linker_for_language(self, language: str) -> str:
        """CUDA linking is typically handled by the host C++ compiler."""
        # nvcc can link, but usually we delegate to the C++ toolchain
        if language == "cuda":
            # Return cuda so the resolver knows to use nvcc if needed
            return "cuda"
        return super()._linker_for_language(language)


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
)
