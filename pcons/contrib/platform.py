# SPDX-License-Identifier: MIT
"""Platform-specific helpers for pcons modules.

This module provides utilities for platform detection and platform-specific
path/extension handling that are useful for add-on modules.

Note: Core platform detection is in pcons.configure.platform. This module
provides higher-level helpers specifically for module authors.
"""

from __future__ import annotations

import platform
import sys


def get_platform_name() -> str:
    """Get the current platform name.

    Returns:
        Platform name: "darwin", "linux", or "win32".
    """
    return sys.platform


def is_macos() -> bool:
    """Check if running on macOS."""
    return sys.platform == "darwin"


def is_linux() -> bool:
    """Check if running on Linux."""
    return sys.platform.startswith("linux")


def is_windows() -> bool:
    """Check if running on Windows."""
    return sys.platform == "win32"


def get_arch() -> str:
    """Get the current machine architecture.

    Returns:
        Architecture name (e.g., "x86_64", "arm64").
    """
    machine = platform.machine().lower()
    # Normalize common names
    if machine in ("amd64", "x86_64"):
        return "x86_64"
    elif machine in ("arm64", "aarch64"):
        return "arm64"
    elif machine in ("i386", "i686", "x86"):
        return "x86"
    return machine


def get_shared_lib_extension() -> str:
    """Get the shared library extension for the current platform.

    Returns:
        Extension including the dot: ".dylib", ".so", or ".dll".
    """
    if sys.platform == "darwin":
        return ".dylib"
    elif sys.platform == "win32":
        return ".dll"
    else:
        return ".so"


def get_static_lib_extension() -> str:
    """Get the static library extension for the current platform.

    Returns:
        Extension including the dot: ".a" or ".lib".
    """
    if sys.platform == "win32":
        return ".lib"
    return ".a"


def get_executable_extension() -> str:
    """Get the executable extension for the current platform.

    Returns:
        Extension including the dot, or empty string for Unix.
    """
    if sys.platform == "win32":
        return ".exe"
    return ""


def get_shared_lib_prefix() -> str:
    """Get the shared library prefix for the current platform.

    Returns:
        Prefix: "lib" on Unix, empty string on Windows.
    """
    if sys.platform == "win32":
        return ""
    return "lib"


def get_static_lib_prefix() -> str:
    """Get the static library prefix for the current platform.

    Returns:
        Prefix: "lib" on Unix, empty string on Windows.
    """
    if sys.platform == "win32":
        return ""
    return "lib"


def format_shared_lib_name(name: str) -> str:
    """Format a library name as a shared library filename.

    Args:
        name: Base library name (e.g., "foo").

    Returns:
        Full shared library filename (e.g., "libfoo.so", "foo.dll").

    Example:
        >>> format_shared_lib_name("mylib")
        'libmylib.dylib'  # on macOS
        'libmylib.so'     # on Linux
        'mylib.dll'       # on Windows
    """
    prefix = get_shared_lib_prefix()
    ext = get_shared_lib_extension()
    return f"{prefix}{name}{ext}"


def format_static_lib_name(name: str) -> str:
    """Format a library name as a static library filename.

    Args:
        name: Base library name (e.g., "foo").

    Returns:
        Full static library filename (e.g., "libfoo.a", "foo.lib").

    Example:
        >>> format_static_lib_name("mylib")
        'libmylib.a'  # on Unix
        'mylib.lib'   # on Windows
    """
    prefix = get_static_lib_prefix()
    ext = get_static_lib_extension()
    return f"{prefix}{name}{ext}"


def get_path_separator() -> str:
    """Get the path separator for the current platform.

    Returns:
        Path separator: ":" on Unix, ";" on Windows.
    """
    if sys.platform == "win32":
        return ";"
    return ":"
