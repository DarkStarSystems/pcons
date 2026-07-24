# SPDX-License-Identifier: MIT
"""Compiler family identification.

Classifies a compiler binary into a pcons toolchain family, used when the
conventional selection environment variables (``CC``/``CXX``) must steer
toolchain auto-detection or be validated against an explicitly requested
toolchain. Name-based guesses are unreliable (macOS ``gcc``/``g++`` are
Apple clang shims), so classification prefers the ``--version`` output.
"""

from __future__ import annotations

import subprocess
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=32)
def compiler_family(path: str) -> str | None:
    """Classify a compiler binary: "gcc", "llvm", "clang-cl", "msvc", or None.

    The returned name matches the pcons toolchain registry alias for the
    binary's family. Returns None when the binary can't be classified
    (e.g. a compiler-wrapper script) — callers should treat None as
    "no opinion", not an error.
    """
    stem = Path(path).stem.lower()
    # Unambiguous basenames first; clang-cl identifies as plain clang in
    # --version output, and cl.exe has no --version at all.
    if "clang-cl" in stem:
        return "clang-cl"
    if stem == "cl":
        return "msvc"

    out = _version_output(path)
    lowered = out.lower()
    if "clang" in lowered:  # covers "Apple clang" and "clang version"
        return "llvm"
    if "free software foundation" in lowered or "(gcc)" in lowered:
        return "gcc"
    if "microsoft" in lowered:
        return "msvc"

    # Unrecognized output (or none): fall back to the basename.
    if "clang" in stem:
        return "llvm"
    if "gcc" in stem or "g++" in stem:
        return "gcc"
    return None


def _version_output(path: str) -> str:
    """Combined stdout+stderr of ``<path> --version``, or "" on failure."""
    try:
        proc = subprocess.run(
            [path, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return (proc.stdout or "") + (proc.stderr or "")
