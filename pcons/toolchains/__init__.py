# SPDX-License-Identifier: MIT
"""Toolchain definitions (GCC, LLVM, MSVC, Cython, etc.).

Toolchains self-register when imported. The find_*_toolchain() functions
use the registry to discover available toolchains without hardcoding
toolchain-specific information here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

# Build context classes for C/C++ toolchains
from pcons.toolchains.build_context import (
    CompileLinkContext,
    MsvcCompileLinkContext,
)
from pcons.toolchains.clang_cl import (
    ClangClCCompiler,
    ClangClCxxCompiler,
    ClangClLibrarian,
    ClangClLinker,
    ClangClToolchain,
)

# Import toolchain modules to trigger registration
# These imports cause each toolchain to register itself
from pcons.toolchains.cuda import CudaToolchain, find_cuda_toolchain
from pcons.toolchains.cython import (
    CythonCCompiler,
    CythonLinker,
    CythonToolchain,
    CythonTranspiler,
)
from pcons.toolchains.gcc import (
    GccArchiver,
    GccCCompiler,
    GccCxxCompiler,
    GccLinker,
    GccToolchain,
)
from pcons.toolchains.llvm import (
    ClangCCompiler,
    ClangCxxCompiler,
    LlvmArchiver,
    LlvmLinker,
    LlvmToolchain,
)
from pcons.toolchains.msvc import (
    MsvcCompiler,
    MsvcLibrarian,
    MsvcLinker,
    MsvcToolchain,
)
from pcons.tools.cuda import CudaCompiler

# Re-export the registry for users who want to register custom toolchains
from pcons.tools.toolchain import toolchain_registry

if TYPE_CHECKING:
    from pcons.tools.toolchain import BaseToolchain


def find_c_toolchain(
    prefer: list[str] | None = None,
) -> BaseToolchain:
    """Find the first available C/C++ toolchain.

    Tries toolchains in order of preference and returns the first
    one that is available on the system. Checks for compiler executables
    in PATH and sets up the toolchain's tools.

    This function queries the toolchain registry, which toolchains populate
    when their modules are imported. Users can register custom toolchains
    using toolchain_registry.register().

    Args:
        prefer: List of toolchain names to try, in order.
                Defaults to platform-appropriate order:
                - Windows: ["clang-cl", "msvc", "llvm", "gcc"]
                - Others: ["llvm", "gcc"]

    Returns:
        A configured toolchain ready for use.

    Raises:
        RuntimeError: If no toolchain is available.

    Example:
        from pcons.toolchains import find_c_toolchain

        toolchain = find_c_toolchain()
        env = project.Environment(toolchain=toolchain)

    To register a custom toolchain:
        from pcons.toolchains import toolchain_registry

        toolchain_registry.register(
            MyToolchain,
            aliases=["my-toolchain"],
            check_command="my-cc",
            tool_classes=[MyCompiler, MyLinker],
            category="c",
        )
    """
    if prefer is None:
        import sys

        if sys.platform == "win32":
            # On Windows, prefer MSVC-compatible toolchains
            prefer = ["clang-cl", "msvc", "llvm", "gcc"]
        else:
            prefer = ["llvm", "gcc"]

    toolchain = toolchain_registry.find_available("c", prefer)
    if toolchain is not None:
        return toolchain

    # Build error message with tried names
    tried = toolchain_registry.get_tried_names("c", prefer)
    raise RuntimeError(
        f"No C/C++ toolchain found. Tried: {', '.join(tried)}. "
        "Make sure a compiler (clang, clang-cl, gcc, or MSVC) is installed and in PATH."
    )


__all__ = [
    # Toolchain finder and registry
    "find_c_toolchain",
    "find_cuda_toolchain",
    "toolchain_registry",
    # Build context classes
    "CompileLinkContext",
    "MsvcCompileLinkContext",
    # CUDA toolchain
    "CudaCompiler",
    "CudaToolchain",
    # Cython toolchain
    "CythonCCompiler",
    "CythonLinker",
    "CythonToolchain",
    "CythonTranspiler",
    # GCC toolchain
    "GccCCompiler",
    "GccCxxCompiler",
    "GccArchiver",
    "GccLinker",
    "GccToolchain",
    # LLVM toolchain
    "ClangCCompiler",
    "ClangCxxCompiler",
    "LlvmArchiver",
    "LlvmLinker",
    "LlvmToolchain",
    # Clang-CL toolchain (MSVC-compatible)
    "ClangClCCompiler",
    "ClangClCxxCompiler",
    "ClangClLibrarian",
    "ClangClLinker",
    "ClangClToolchain",
    # MSVC toolchain
    "MsvcCompiler",
    "MsvcLibrarian",
    "MsvcLinker",
    "MsvcToolchain",
]
