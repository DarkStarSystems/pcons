# SPDX-License-Identifier: MIT
"""Package finder that searches standard system paths.

This finder looks for libraries and headers in standard system locations
without relying on pkg-config. It's a fallback when pkg-config files
aren't available.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import TYPE_CHECKING

from pcons.configure.platform import get_platform
from pcons.packages.description import PackageDescription
from pcons.packages.finders.base import BaseFinder

if TYPE_CHECKING:
    pass


# Known package aliases and library mappings
PACKAGE_ALIASES: dict[str, dict[str, list[str] | str]] = {
    "zlib": {
        "headers": ["zlib.h"],
        "libraries": ["z"],
    },
    "openssl": {
        "headers": ["openssl/ssl.h", "openssl/crypto.h"],
        "libraries": ["ssl", "crypto"],
    },
    "pthread": {
        "headers": ["pthread.h"],
        "libraries": ["pthread"],
        "defines": ["_REENTRANT"],
    },
    "dl": {
        "headers": ["dlfcn.h"],
        "libraries": ["dl"],
    },
    "m": {
        "headers": ["math.h"],
        "libraries": ["m"],
    },
    "rt": {
        "headers": ["time.h"],
        "libraries": ["rt"],
    },
    "curl": {
        "headers": ["curl/curl.h"],
        "libraries": ["curl"],
    },
    "sqlite3": {
        "headers": ["sqlite3.h"],
        "libraries": ["sqlite3"],
    },
    "expat": {
        "headers": ["expat.h"],
        "libraries": ["expat"],
    },
    "png": {
        "headers": ["png.h"],
        "libraries": ["png"],
    },
    "jpeg": {
        "headers": ["jpeglib.h"],
        "libraries": ["jpeg"],
    },
}


class SystemFinder(BaseFinder):
    """Find packages by searching standard system paths.

    This finder searches for headers and libraries in standard locations:
    - /usr/include, /usr/local/include
    - /usr/lib, /usr/local/lib
    - Platform-specific paths (e.g., /opt/homebrew on macOS ARM)

    It uses a database of known packages (PACKAGE_ALIASES) to map
    package names to header files and library names.

    Example:
        finder = SystemFinder()
        zlib = finder.find("zlib")
        if zlib:
            print(f"Found zlib headers in {zlib.include_dirs}")
            print(f"Libraries: {zlib.libraries}")

    Attributes:
        include_paths: Paths to search for headers.
        library_paths: Paths to search for libraries.
    """

    def __init__(
        self,
        include_paths: list[Path] | None = None,
        library_paths: list[Path] | None = None,
    ) -> None:
        """Create a SystemFinder.

        Args:
            include_paths: Custom include paths to search.
            library_paths: Custom library paths to search.
        """
        self._platform = get_platform()

        if include_paths is not None:
            self.include_paths = include_paths
        else:
            self.include_paths = self._default_include_paths()

        if library_paths is not None:
            self.library_paths = library_paths
        else:
            self.library_paths = self._default_library_paths()

    def _default_include_paths(self) -> list[Path]:
        """Get default include search paths for this platform."""
        paths: list[Path] = []

        # Environment variable
        if "CPATH" in os.environ:
            paths.extend(Path(p) for p in os.environ["CPATH"].split(os.pathsep))
        if "C_INCLUDE_PATH" in os.environ:
            paths.extend(
                Path(p) for p in os.environ["C_INCLUDE_PATH"].split(os.pathsep)
            )

        if self._platform.is_macos:
            # Homebrew on ARM Mac
            paths.append(Path("/opt/homebrew/include"))
            # Homebrew on Intel Mac
            paths.append(Path("/usr/local/include"))
            # Xcode SDK headers
            paths.append(Path("/usr/include"))
        elif self._platform.is_linux:
            paths.append(Path("/usr/local/include"))
            paths.append(Path("/usr/include"))
            # Multi-arch paths
            arch = os.uname().machine
            paths.append(Path(f"/usr/include/{arch}-linux-gnu"))
        elif self._platform.is_windows:
            # Common Windows SDK locations
            program_files = Path(os.environ.get("ProgramFiles", "C:\\Program Files"))
            paths.append(program_files / "Windows Kits" / "10" / "Include")
            # vcpkg default location
            paths.append(Path("C:\\vcpkg\\installed\\x64-windows\\include"))

        return [p for p in paths if p.exists()]

    def _default_library_paths(self) -> list[Path]:
        """Get default library search paths for this platform."""
        paths: list[Path] = []

        # Environment variable
        if "LIBRARY_PATH" in os.environ:
            paths.extend(Path(p) for p in os.environ["LIBRARY_PATH"].split(os.pathsep))

        if self._platform.is_macos:
            # Homebrew on ARM Mac
            paths.append(Path("/opt/homebrew/lib"))
            # Homebrew on Intel Mac
            paths.append(Path("/usr/local/lib"))
            paths.append(Path("/usr/lib"))
        elif self._platform.is_linux:
            paths.append(Path("/usr/local/lib"))
            paths.append(Path("/usr/lib"))
            paths.append(Path("/lib"))
            # Multi-arch paths
            arch = os.uname().machine
            paths.append(Path(f"/usr/lib/{arch}-linux-gnu"))
            paths.append(Path("/usr/lib64"))
        elif self._platform.is_windows:
            program_files = Path(os.environ.get("ProgramFiles", "C:\\Program Files"))
            paths.append(program_files / "Windows Kits" / "10" / "Lib")
            # vcpkg default location
            paths.append(Path("C:\\vcpkg\\installed\\x64-windows\\lib"))

        return [p for p in paths if p.exists()]

    @property
    def name(self) -> str:
        return "system"

    def _find_header(self, header: str) -> Path | None:
        """Find a header file in include paths.

        Args:
            header: Header file path (e.g., "zlib.h", "openssl/ssl.h").

        Returns:
            Base include directory if found, None otherwise.
        """
        for inc_path in self.include_paths:
            full_path = inc_path / header
            if full_path.exists():
                return inc_path
        return None

    def _find_library(self, lib_name: str) -> Path | None:
        """Find a library in library paths.

        Args:
            lib_name: Library name without prefix/suffix (e.g., "z", "ssl").

        Returns:
            Library directory if found, None otherwise.
        """
        # Generate possible library file names
        lib_patterns: list[str] = []
        if self._platform.is_windows:
            lib_patterns = [f"{lib_name}.lib", f"lib{lib_name}.lib"]
        elif self._platform.is_macos:
            lib_patterns = [
                f"lib{lib_name}.dylib",
                f"lib{lib_name}.a",
                f"lib{lib_name}.tbd",
            ]
        else:  # Linux
            lib_patterns = [
                f"lib{lib_name}.so",
                f"lib{lib_name}.a",
            ]
            # Also check for versioned .so files
            lib_patterns.append(f"lib{lib_name}.so.*")

        for lib_path in self.library_paths:
            for pattern in lib_patterns:
                if "*" in pattern:
                    # Glob pattern
                    matches = list(lib_path.glob(pattern))
                    if matches:
                        return lib_path
                else:
                    full_path = lib_path / pattern
                    if full_path.exists():
                        return lib_path
        return None

    def _extract_version(self, header_path: Path, header_name: str) -> str:
        """Try to extract version from a header file.

        Args:
            header_path: Include directory containing the header.
            header_name: Name of the header file.

        Returns:
            Version string if found, empty string otherwise.
        """
        full_path = header_path / header_name
        if not full_path.exists():
            return ""

        try:
            content = full_path.read_text(errors="ignore")
            # Look for common version patterns
            patterns = [
                # #define ZLIB_VERSION "1.2.13"
                r'#define\s+\w*VERSION\w*\s+"([^"]+)"',
                # #define MAJOR 1
                # #define MINOR 2
                r"#define\s+\w*MAJOR\w*\s+(\d+).*#define\s+\w*MINOR\w*\s+(\d+)",
            ]
            for pattern in patterns:
                match = re.search(pattern, content, re.MULTILINE | re.DOTALL)
                if match:
                    if len(match.groups()) == 1:
                        return match.group(1)
                    elif len(match.groups()) >= 2:
                        return f"{match.group(1)}.{match.group(2)}"
        except OSError:
            pass

        return ""

    def find(
        self,
        package_name: str,
        version: str | None = None,
        components: list[str] | None = None,
    ) -> PackageDescription | None:
        """Find a package by searching system paths.

        Args:
            package_name: Package name (must be in PACKAGE_ALIASES).
            version: Not used (we can't easily check versions).
            components: Not used by system finder.

        Returns:
            PackageDescription if found, None otherwise.
        """
        # Look up package info
        pkg_info = PACKAGE_ALIASES.get(package_name)
        if pkg_info is None:
            return None

        headers = pkg_info.get("headers", [])
        libraries = pkg_info.get("libraries", [])
        defines = pkg_info.get("defines", [])

        if isinstance(headers, str):
            headers = [headers]
        if isinstance(libraries, str):
            libraries = [libraries]
        if isinstance(defines, str):
            defines = [defines]

        # Find headers
        include_dirs: list[str] = []
        for header in headers:
            inc_dir = self._find_header(header)
            if inc_dir is None:
                return None  # Required header not found
            if str(inc_dir) not in include_dirs:
                include_dirs.append(str(inc_dir))

        # Find libraries
        library_dirs: list[str] = []
        found_libs: list[str] = []
        for lib in libraries:
            lib_dir = self._find_library(lib)
            if lib_dir is None:
                return None  # Required library not found
            if str(lib_dir) not in library_dirs:
                library_dirs.append(str(lib_dir))
            found_libs.append(lib)

        # Try to extract version from first header
        pkg_version = ""
        if headers and include_dirs:
            pkg_version = self._extract_version(Path(include_dirs[0]), headers[0])

        return PackageDescription(
            name=package_name,
            version=pkg_version,
            include_dirs=include_dirs,
            library_dirs=library_dirs,
            libraries=found_libs,
            defines=list(defines),
            found_by="system",
        )
