# SPDX-License-Identifier: MIT
"""Package finder using pkg-config.

This finder uses the pkg-config tool to locate packages on the system.
It's the most reliable way to find packages on Unix-like systems.
"""

from __future__ import annotations

import re
import shlex
import shutil
import subprocess
from typing import TYPE_CHECKING

from pcons.packages.description import ComponentDescription, PackageDescription
from pcons.packages.finders.base import BaseFinder

if TYPE_CHECKING:
    pass


class PkgConfigFinder(BaseFinder):
    """Find packages using pkg-config.

    This finder calls the pkg-config command-line tool to discover
    package information. It supports version requirements and
    automatically parses the output into a PackageDescription.

    Example:
        finder = PkgConfigFinder()
        if finder.is_available():
            zlib = finder.find("zlib", version=">=1.2")
            if zlib:
                print(f"Found zlib {zlib.version}")
                print(f"CFLAGS: {' '.join(zlib.get_compile_flags())}")
                print(f"LIBS: {' '.join(zlib.get_link_flags())}")

    Attributes:
        pkg_config_cmd: Command to invoke pkg-config (default: "pkg-config").
    """

    def __init__(self, pkg_config_cmd: str = "pkg-config") -> None:
        """Create a PkgConfigFinder.

        Args:
            pkg_config_cmd: Path to pkg-config command.
        """
        self.pkg_config_cmd = pkg_config_cmd
        self._pkg_config_path: str | None = None

    @property
    def name(self) -> str:
        return "pkg-config"

    def is_available(self) -> bool:
        """Check if pkg-config is available."""
        if self._pkg_config_path is None:
            self._pkg_config_path = shutil.which(self.pkg_config_cmd) or ""
        return bool(self._pkg_config_path)

    def _run_pkg_config(self, *args: str) -> tuple[bool, str]:
        """Run pkg-config with arguments.

        Args:
            *args: Arguments to pass to pkg-config.

        Returns:
            Tuple of (success, output).
        """
        try:
            result = subprocess.run(
                [self.pkg_config_cmd, *args],
                capture_output=True,
                text=True,
            )
            return result.returncode == 0, result.stdout.strip()
        except OSError:
            return False, ""

    def _parse_flags(self, flags_str: str) -> list[str]:
        """Parse a flags string into a list.

        Handles shell quoting properly.
        """
        if not flags_str:
            return []
        return shlex.split(flags_str)

    def _extract_includes(self, flags: list[str]) -> tuple[list[str], list[str]]:
        """Extract -I flags from a list of flags.

        Returns:
            Tuple of (include_dirs, remaining_flags).
        """
        includes: list[str] = []
        remaining: list[str] = []
        for flag in flags:
            if flag.startswith("-I"):
                includes.append(flag[2:])
            else:
                remaining.append(flag)
        return includes, remaining

    def _extract_defines(self, flags: list[str]) -> tuple[list[str], list[str]]:
        """Extract -D flags from a list of flags.

        Returns:
            Tuple of (defines, remaining_flags).
        """
        defines: list[str] = []
        remaining: list[str] = []
        for flag in flags:
            if flag.startswith("-D"):
                defines.append(flag[2:])
            else:
                remaining.append(flag)
        return defines, remaining

    def _extract_library_dirs(self, flags: list[str]) -> tuple[list[str], list[str]]:
        """Extract -L flags from a list of flags.

        Returns:
            Tuple of (library_dirs, remaining_flags).
        """
        libdirs: list[str] = []
        remaining: list[str] = []
        for flag in flags:
            if flag.startswith("-L"):
                libdirs.append(flag[2:])
            else:
                remaining.append(flag)
        return libdirs, remaining

    def _extract_libraries(self, flags: list[str]) -> tuple[list[str], list[str]]:
        """Extract -l flags from a list of flags.

        Returns:
            Tuple of (libraries, remaining_flags).
        """
        libs: list[str] = []
        remaining: list[str] = []
        for flag in flags:
            if flag.startswith("-l"):
                libs.append(flag[2:])
            else:
                remaining.append(flag)
        return libs, remaining

    def find(
        self,
        package_name: str,
        version: str | None = None,
        components: list[str] | None = None,
    ) -> PackageDescription | None:
        """Find a package using pkg-config.

        Args:
            package_name: Name of the .pc file (without .pc suffix).
            version: Optional version requirement (e.g., ">=1.2", "=1.0").
            components: Not used by pkg-config, but accepted for API compatibility.

        Returns:
            PackageDescription if found, None otherwise.
        """
        if not self.is_available():
            return None

        # Check if package exists
        success, _ = self._run_pkg_config("--exists", package_name)
        if not success:
            return None

        # If version specified, check version constraint
        if version:
            # Parse version constraint
            match = re.match(r"(>=|<=|=|>|<)?(.+)", version)
            if match:
                op = match.group(1) or "="
                ver = match.group(2)

                pkg_config_ops = {
                    ">=": "--atleast-version",
                    ">": "--atleast-version",  # pkg-config doesn't have >
                    "<=": "--max-version",
                    "<": "--max-version",  # pkg-config doesn't have <
                    "=": "--exact-version",
                }
                op_flag = pkg_config_ops.get(op, "--atleast-version")
                success, _ = self._run_pkg_config(op_flag + "=" + ver, package_name)
                if not success:
                    return None

        # Get version
        success, version_str = self._run_pkg_config("--modversion", package_name)
        if not success:
            version_str = ""

        # Get cflags
        success, cflags_str = self._run_pkg_config("--cflags", package_name)
        cflags = self._parse_flags(cflags_str) if success else []

        # Get libs
        success, libs_str = self._run_pkg_config("--libs", package_name)
        libs = self._parse_flags(libs_str) if success else []

        # Parse cflags
        include_dirs, cflags = self._extract_includes(cflags)
        defines, compile_flags = self._extract_defines(cflags)

        # Parse libs
        library_dirs, libs = self._extract_library_dirs(libs)
        libraries, link_flags = self._extract_libraries(libs)

        # Get prefix if available
        success, prefix = self._run_pkg_config("--variable=prefix", package_name)
        if not success:
            prefix = ""

        # Get requires (dependencies)
        success, requires_str = self._run_pkg_config("--print-requires", package_name)
        dependencies: list[str] = []
        if success and requires_str:
            # Parse requires, which may include version constraints
            for line in requires_str.split("\n"):
                if line.strip():
                    # Take just the package name, not the version constraint
                    dep_name = line.split()[0]
                    dependencies.append(dep_name)

        # Create components if requested
        pkg_components: dict[str, ComponentDescription] = {}
        if components:
            for comp_name in components:
                # Try to find component as separate pkg-config package
                comp_pkg_name = f"{package_name}-{comp_name}"
                success, _ = self._run_pkg_config("--exists", comp_pkg_name)
                if success:
                    # Recursively find the component
                    comp_desc = self.find(comp_pkg_name)
                    if comp_desc:
                        pkg_components[comp_name] = ComponentDescription(
                            name=comp_name,
                            include_dirs=comp_desc.include_dirs,
                            library_dirs=comp_desc.library_dirs,
                            libraries=comp_desc.libraries,
                            defines=comp_desc.defines,
                            compile_flags=comp_desc.compile_flags,
                            link_flags=comp_desc.link_flags,
                        )

        return PackageDescription(
            name=package_name,
            version=version_str,
            include_dirs=include_dirs,
            library_dirs=library_dirs,
            libraries=libraries,
            defines=defines,
            compile_flags=compile_flags,
            link_flags=link_flags,
            dependencies=dependencies,
            components=pkg_components,
            prefix=prefix,
            found_by="pkg-config",
        )
