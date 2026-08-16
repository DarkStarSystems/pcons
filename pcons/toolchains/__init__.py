# SPDX-License-Identifier: MIT
"""Toolchain definitions (GCC, LLVM, MSVC, Cython, etc.).

Toolchains self-register when imported, and this package imports them
lazily: each family is declared to the registry by name (`register_lazy`
below), and the registry imports its module the first time the name is
looked up or its category is searched. A plain `import pcons` therefore
pays for no toolchain module at all; `find_c_toolchain()` pulls in only
the families it actually tries.

The find_*_toolchain() functions use the registry to discover available
toolchains without hardcoding toolchain-specific information here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

# Re-export the registry for users who want to register custom toolchains
from pcons.tools.toolchain import toolchain_registry

if TYPE_CHECKING:
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
    from pcons.toolchains.qt import (
        QtPackage,
        QtTool,
        QtToolchain,
        find_qt,
        find_qt_toolchain,
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
    from pcons.tools.toolchain import BaseToolchain


# Which module registers which names. Importing the module is what puts the
# real entry (and any finder it declares) in the registry.
toolchain_registry.register_lazy(
    ["gcc", "gnu"],
    "pcons.toolchains.gcc",
    category="c",
    description="GNU Compiler Collection (gcc/g++)",
)
toolchain_registry.register_lazy(
    ["llvm", "clang"],
    "pcons.toolchains.llvm",
    category="c",
    description="LLVM/Clang compiler",
)
toolchain_registry.register_lazy(
    ["msvc", "vc", "visualstudio"],
    "pcons.toolchains.msvc",
    category="c",
    description="Microsoft Visual C/C++ compiler",
)
toolchain_registry.register_lazy(
    ["clang-cl"],
    "pcons.toolchains.clang_cl",
    category="c",
    description="Clang with MSVC-compatible flags",
)
toolchain_registry.register_lazy(
    ["cuda", "nvcc"],
    "pcons.toolchains.cuda",
    category="cuda",
    description="NVIDIA CUDA compiler (nvcc)",
)
toolchain_registry.register_lazy(
    ["cython"],
    "pcons.toolchains.cython",
    category="python",
    description="Cython transpiler (.pyx to Python extension)",
)
toolchain_registry.register_lazy(
    ["emscripten", "emcc"],
    "pcons.toolchains.emscripten",
    category="wasm",
    description="Emscripten WebAssembly toolchain",
)
toolchain_registry.register_lazy(
    ["wasi", "wasi-sdk"],
    "pcons.toolchains.wasi",
    category="wasm",
    description="WASI SDK for standalone WebAssembly (.wasm)",
)
toolchain_registry.register_lazy(
    ["gfortran", "fortran"],
    "pcons.toolchains.gfortran",
    category="fortran",
    description="GNU Fortran compiler",
)
toolchain_registry.register_lazy(
    ["swiftc", "swift"],
    "pcons.toolchains.swift",
    category="swift",
    description="Swift compiler (whole-module compilation, swiftc links)",
)
toolchain_registry.register_lazy(
    ["qt", "qt6"],
    "pcons.toolchains.qt",
    category="qt",
    description="Qt 6 tools (moc/uic/rcc) atop a C++ toolchain",
)


def _env_selected_c_toolchain() -> BaseToolchain | None:
    """C/C++ toolchain implied by the $CXX/$CC env vars, or None.

    When either variable names a classifiable compiler, that compiler's
    family wins auto-detection outright (bypassing the availability probe
    for the family's default command names — the user's compiler existing
    is the availability proof). An unclassifiable value (a wrapper script)
    returns None: normal detection proceeds, and the override still lands
    on whichever toolchain wins via Tool.env_var.
    """
    from pcons.configure.compiler_id import compiler_family
    from pcons.tools.tool import resolve_env_cmd_override

    for var in ("CXX", "CC"):
        path = resolve_env_cmd_override(var)
        if path is None:
            continue
        family = compiler_family(path)
        if family is None:
            return None
        entry = toolchain_registry.get(family)
        if entry is not None:
            return entry.create_toolchain()
    return None


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
        # $CXX/$CC steer auto-detection: the user named a compiler, so use
        # the toolchain family that compiler belongs to (see docs).
        env_toolchain = _env_selected_c_toolchain()
        if env_toolchain is not None:
            return env_toolchain
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


# Where each lazily-exported name lives. `__getattr__` below imports the
# module on first attribute access (PEP 562), so `from pcons.toolchains
# import GccToolchain` still works without loading every toolchain.
_LAZY_ATTRS = {
    # Build context classes
    "CompileLinkContext": "pcons.toolchains.build_context",
    "MsvcCompileLinkContext": "pcons.toolchains.build_context",
    # CUDA toolchain
    "CudaCompiler": "pcons.tools.cuda",
    "CudaToolchain": "pcons.toolchains.cuda",
    "find_cuda_toolchain": "pcons.toolchains.cuda",
    # Cython toolchain
    "CythonCCompiler": "pcons.toolchains.cython",
    "CythonLinker": "pcons.toolchains.cython",
    "CythonToolchain": "pcons.toolchains.cython",
    "CythonTranspiler": "pcons.toolchains.cython",
    "find_cython_toolchain": "pcons.toolchains.cython",
    # GFortran toolchain
    "GfortranCompiler": "pcons.toolchains.gfortran",
    "GfortranLinker": "pcons.toolchains.gfortran",
    "GfortranToolchain": "pcons.toolchains.gfortran",
    "find_fortran_toolchain": "pcons.toolchains.gfortran",
    # Swift toolchain
    "SwiftCompiler": "pcons.toolchains.swift",
    "SwiftLinker": "pcons.toolchains.swift",
    "SwiftToolchain": "pcons.toolchains.swift",
    "clang_module_map": "pcons.toolchains.swift",
    "find_swift_toolchain": "pcons.toolchains.swift",
    # GCC toolchain
    "GccCCompiler": "pcons.toolchains.gcc",
    "GccCxxCompiler": "pcons.toolchains.gcc",
    "GccArchiver": "pcons.toolchains.gcc",
    "GccLinker": "pcons.toolchains.gcc",
    "GccToolchain": "pcons.toolchains.gcc",
    # LLVM toolchain
    "ClangCCompiler": "pcons.toolchains.llvm",
    "ClangCxxCompiler": "pcons.toolchains.llvm",
    "LlvmArchiver": "pcons.toolchains.llvm",
    "LlvmLinker": "pcons.toolchains.llvm",
    "LlvmToolchain": "pcons.toolchains.llvm",
    # Clang-CL toolchain (MSVC-compatible)
    "ClangClCCompiler": "pcons.toolchains.clang_cl",
    "ClangClCxxCompiler": "pcons.toolchains.clang_cl",
    "ClangClLibrarian": "pcons.toolchains.clang_cl",
    "ClangClLinker": "pcons.toolchains.clang_cl",
    "ClangClToolchain": "pcons.toolchains.clang_cl",
    # MSVC toolchain
    "MsvcCompiler": "pcons.toolchains.msvc",
    "MsvcLibrarian": "pcons.toolchains.msvc",
    "MsvcLinker": "pcons.toolchains.msvc",
    "MsvcToolchain": "pcons.toolchains.msvc",
    # Qt toolchain
    "QtPackage": "pcons.toolchains.qt",
    "QtTool": "pcons.toolchains.qt",
    "QtToolchain": "pcons.toolchains.qt",
    "find_qt": "pcons.toolchains.qt",
    "find_qt_toolchain": "pcons.toolchains.qt",
    # Emscripten toolchain
    "EmccCCompiler": "pcons.toolchains.emscripten",
    "EmccCxxCompiler": "pcons.toolchains.emscripten",
    "EmccArchiver": "pcons.toolchains.emscripten",
    "EmccLinker": "pcons.toolchains.emscripten",
    "EmscriptenToolchain": "pcons.toolchains.emscripten",
    "find_emscripten_toolchain": "pcons.toolchains.emscripten",
    # WASI toolchain
    "WasiCCompiler": "pcons.toolchains.wasi",
    "WasiCxxCompiler": "pcons.toolchains.wasi",
    "WasiArchiver": "pcons.toolchains.wasi",
    "WasiLinker": "pcons.toolchains.wasi",
    "WasiToolchain": "pcons.toolchains.wasi",
    "find_wasi_toolchain": "pcons.toolchains.wasi",
}


def __getattr__(name: str) -> object:
    module_name = _LAZY_ATTRS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    return getattr(importlib.import_module(module_name), name)


def __dir__() -> list[str]:
    return sorted(__all__)


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
    # Qt toolchain
    "QtPackage",
    "QtTool",
    "QtToolchain",
    "find_qt",
    "find_qt_toolchain",
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
