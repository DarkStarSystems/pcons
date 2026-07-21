# SPDX-License-Identifier: MIT
"""Toolchain definitions (GCC, LLVM, MSVC, Cython, etc.).

Toolchains self-register when imported. The find_*_toolchain() functions
use the registry to discover available toolchains without hardcoding
toolchain-specific information here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

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

# Importing each toolchain module triggers its self-registration
from pcons.toolchains.cuda import CudaToolchain, find_cuda_toolchain
from pcons.toolchains.cython import (
    CythonCCompiler,
    CythonLinker,
    CythonToolchain,
    CythonTranspiler,
    find_cython_toolchain,
)
from pcons.toolchains.emscripten import (
    EmccArchiver,
    EmccCCompiler,
    EmccCxxCompiler,
    EmccLinker,
    EmscriptenToolchain,
    find_emscripten_toolchain,
)
from pcons.toolchains.gcc import (
    GccArchiver,
    GccCCompiler,
    GccCxxCompiler,
    GccLinker,
    GccToolchain,
)
from pcons.toolchains.gfortran import (
    GfortranCompiler,
    GfortranLinker,
    GfortranToolchain,
    find_fortran_toolchain,
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
from pcons.toolchains.swift import (
    SwiftCompiler,
    SwiftLinker,
    SwiftToolchain,
    clang_module_map,
    find_swift_toolchain,
)
from pcons.toolchains.wasi import (
    WasiArchiver,
    WasiCCompiler,
    WasiCxxCompiler,
    WasiLinker,
    WasiToolchain,
    find_wasi_toolchain,
)
from pcons.tools.cuda import CudaCompiler

# Re-export the registry for users who want to register custom toolchains
from pcons.tools.toolchain import toolchain_registry

if TYPE_CHECKING:
    from pcons.tools.toolchain import BaseToolchain


def find_c_toolchain(
    prefer: list[str] | None = None,
) -> BaseToolchain:
    """Find the first available C/C++ toolchain from the registry.

    Args:
        prefer: Toolchain names to try, in order. Defaults to
                ["clang-cl", "msvc", "llvm", "gcc"] on Windows,
                ["llvm", "gcc"] elsewhere.

    Returns:
        A configured toolchain ready for use.

    Raises:
        RuntimeError: If no toolchain is available.

    Example:
        toolchain = find_c_toolchain()
        env = project.Environment(toolchain=toolchain)

    Custom toolchains can be added via toolchain_registry.register().
    """
    if prefer is None:
        import sys

        if sys.platform == "win32":
            prefer = ["clang-cl", "msvc", "llvm", "gcc"]
        else:
            prefer = ["llvm", "gcc"]

    toolchain = toolchain_registry.find_available("c", prefer)
    if toolchain is not None:
        return toolchain

    tried = toolchain_registry.get_tried_names("c", prefer)
    raise RuntimeError(
        f"No C/C++ toolchain found. Tried: {', '.join(tried)}. "
        "Make sure a compiler (clang, clang-cl, gcc, or MSVC) is installed and in PATH."
    )


toolchain_registry.register_finder(
    ["c", "c++", "cpp"],
    find_c_toolchain,
    description="Auto-detect a C/C++ toolchain",
)


__all__ = [
    # Toolchain finder and registry
    "find_c_toolchain",
    "find_fortran_toolchain",
    "find_swift_toolchain",
    "clang_module_map",
    "find_cuda_toolchain",
    "find_cython_toolchain",
    "find_emscripten_toolchain",
    "find_wasi_toolchain",
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
    # GFortran toolchain
    "GfortranCompiler",
    "GfortranLinker",
    "GfortranToolchain",
    "SwiftCompiler",
    "SwiftLinker",
    "SwiftToolchain",
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
    # Emscripten toolchain
    "EmccCCompiler",
    "EmccCxxCompiler",
    "EmccArchiver",
    "EmccLinker",
    "EmscriptenToolchain",
    # WASI toolchain
    "WasiCCompiler",
    "WasiCxxCompiler",
    "WasiArchiver",
    "WasiLinker",
    "WasiToolchain",
]
