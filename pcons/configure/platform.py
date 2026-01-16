# SPDX-License-Identifier: MIT
"""Platform detection for pcons.

Detects the current platform (OS, architecture) and provides
platform-specific values like executable suffixes.
"""

from __future__ import annotations

import platform
import struct
from dataclasses import dataclass


@dataclass(frozen=True)
class Platform:
    """Information about the current platform.

    Attributes:
        os: Operating system ('linux', 'darwin', 'windows', 'freebsd', etc.)
        arch: CPU architecture ('x86_64', 'arm64', 'i686', etc.)
        is_64bit: True if running on a 64-bit platform.
        exe_suffix: Suffix for executables ('' or '.exe').
        shared_lib_suffix: Suffix for shared libraries ('.so', '.dylib', '.dll').
        shared_lib_prefix: Prefix for shared libraries ('lib' or '').
        static_lib_suffix: Suffix for static libraries ('.a' or '.lib').
        static_lib_prefix: Prefix for static libraries ('lib' or '').
        object_suffix: Suffix for object files ('.o' or '.obj').
    """

    os: str
    arch: str
    is_64bit: bool
    exe_suffix: str
    shared_lib_suffix: str
    shared_lib_prefix: str
    static_lib_suffix: str
    static_lib_prefix: str
    object_suffix: str

    @property
    def is_windows(self) -> bool:
        """True if running on Windows."""
        return self.os == "windows"

    @property
    def is_macos(self) -> bool:
        """True if running on macOS."""
        return self.os == "darwin"

    @property
    def is_linux(self) -> bool:
        """True if running on Linux."""
        return self.os == "linux"

    @property
    def is_posix(self) -> bool:
        """True if running on a POSIX-like system."""
        return self.os in ("linux", "darwin", "freebsd", "openbsd", "netbsd")

    def shared_lib_name(self, name: str) -> str:
        """Get the full shared library name for a base name.

        Args:
            name: Library base name (e.g., 'foo')

        Returns:
            Full library name (e.g., 'libfoo.so' or 'foo.dll')
        """
        return f"{self.shared_lib_prefix}{name}{self.shared_lib_suffix}"

    def static_lib_name(self, name: str) -> str:
        """Get the full static library name for a base name.

        Args:
            name: Library base name (e.g., 'foo')

        Returns:
            Full library name (e.g., 'libfoo.a' or 'foo.lib')
        """
        return f"{self.static_lib_prefix}{name}{self.static_lib_suffix}"

    def exe_name(self, name: str) -> str:
        """Get the full executable name for a base name.

        Args:
            name: Executable base name (e.g., 'app')

        Returns:
            Full executable name (e.g., 'app' or 'app.exe')
        """
        return f"{name}{self.exe_suffix}"


def detect_platform() -> Platform:
    """Detect the current platform.

    Returns:
        Platform object with detected values.
    """
    # Detect OS
    system = platform.system().lower()
    if system == "darwin":
        os_name = "darwin"
    elif system == "windows":
        os_name = "windows"
    elif system == "linux":
        os_name = "linux"
    elif system == "freebsd":
        os_name = "freebsd"
    else:
        os_name = system

    # Detect architecture
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        arch = "x86_64"
    elif machine in ("arm64", "aarch64"):
        arch = "arm64"
    elif machine in ("i386", "i686", "x86"):
        arch = "i686"
    elif machine.startswith("arm"):
        arch = "arm"
    else:
        arch = machine

    # Detect bitness
    is_64bit = struct.calcsize("P") * 8 == 64

    # Platform-specific suffixes
    if os_name == "windows":
        exe_suffix = ".exe"
        shared_lib_suffix = ".dll"
        shared_lib_prefix = ""
        static_lib_suffix = ".lib"
        static_lib_prefix = ""
        object_suffix = ".obj"
    elif os_name == "darwin":
        exe_suffix = ""
        shared_lib_suffix = ".dylib"
        shared_lib_prefix = "lib"
        static_lib_suffix = ".a"
        static_lib_prefix = "lib"
        object_suffix = ".o"
    else:  # Linux, BSD, etc.
        exe_suffix = ""
        shared_lib_suffix = ".so"
        shared_lib_prefix = "lib"
        static_lib_suffix = ".a"
        static_lib_prefix = "lib"
        object_suffix = ".o"

    return Platform(
        os=os_name,
        arch=arch,
        is_64bit=is_64bit,
        exe_suffix=exe_suffix,
        shared_lib_suffix=shared_lib_suffix,
        shared_lib_prefix=shared_lib_prefix,
        static_lib_suffix=static_lib_suffix,
        static_lib_prefix=static_lib_prefix,
        object_suffix=object_suffix,
    )


# Cached platform instance
_platform: Platform | None = None


def get_platform() -> Platform:
    """Get the cached platform instance.

    Returns:
        The current platform.
    """
    global _platform
    if _platform is None:
        _platform = detect_platform()
    return _platform
