# SPDX-License-Identifier: MIT
"""Toolchain definitions (GCC, LLVM, MSVC, etc.)."""

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

__all__ = [
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
    # MSVC toolchain
    "MsvcCompiler",
    "MsvcLibrarian",
    "MsvcLinker",
    "MsvcToolchain",
]
