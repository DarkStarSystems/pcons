# SPDX-License-Identifier: MIT
"""Package finder using Conan 2.x package manager.

This finder uses Conan to install packages and then parses the generated
pkg-config files to create PackageDescription objects.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pcons.configure.platform import get_platform
from pcons.packages.description import PackageDescription
from pcons.packages.finders.base import BaseFinder
from pcons.packages.finders.pkgconfig import PkgConfigFinder

if TYPE_CHECKING:
    from pcons.configure.config import Configure
    from pcons.tools.toolchain import Toolchain


class ConanFinder(BaseFinder):
    """Find packages using Conan 2.x package manager.

    This finder:
    1. Generates a Conan profile from pcons Environment/Toolchain settings
    2. Runs `conan install` with PkgConfigDeps generator
    3. Parses the resulting .pc files using PkgConfigFinder
    4. Caches results so conan install only runs when needed

    Example:
        from pcons.packages.finders import ConanFinder

        conan = ConanFinder(config, output_folder=build_dir / "deps")
        conan.sync_profile(toolchain, env)
        packages = conan.install()  # Returns dict[str, PackageDescription]

        expat = project.ImportedTarget(packages["expat"])

    Attributes:
        conan_cmd: Command to invoke conan. Can be set via PCONS_CONAN or
            CONAN environment variables, or falls back to "uvx conan" if
            conan is not found but uvx/uv is available.
        output_folder: Directory for conan install outputs.
        profile_path: Path to the generated profile.
    """

    def __init__(
        self,
        config: Configure | None = None,
        *,
        conanfile: str | Path = "conanfile.txt",
        output_folder: str | Path = "deps",
        build_missing: bool = True,
        conan_cmd: str | None = None,
    ) -> None:
        """Create a ConanFinder.

        Args:
            config: Configure context for caching (optional).
            conanfile: Path to conanfile.txt or conanfile.py.
            output_folder: Directory for conan outputs (profiles, .pc files).
            build_missing: If True, build missing packages from source.
            conan_cmd: Path to conan command. If None, checks PCONS_CONAN
                and CONAN env vars, then tries "conan", then "uvx conan".
        """
        self.config = config
        self.conanfile = Path(conanfile)
        self.output_folder = Path(output_folder)
        self.build_missing = build_missing
        self._conan_cmd = conan_cmd
        self._resolved_conan_cmd: list[str] | None = None
        self._platform = get_platform()
        self._profile_settings: dict[str, str] = {}
        self._profile_conf: dict[str, Any] = {}
        self._packages: dict[str, PackageDescription] | None = None

    @property
    def name(self) -> str:
        return "conan"

    @property
    def conan_cmd(self) -> list[str]:
        """Get the conan command as a list (may be ["uvx", "conan"])."""
        if self._resolved_conan_cmd is None:
            self._resolved_conan_cmd = self._resolve_conan_cmd()
        return self._resolved_conan_cmd

    def _resolve_conan_cmd(self) -> list[str]:
        """Resolve the conan command to use.

        Order of precedence:
        1. Explicit conan_cmd passed to constructor
        2. PCONS_CONAN environment variable
        3. CONAN environment variable
        4. "conan" if found in PATH
        5. "uvx conan" if uvx is found in PATH
        6. "uv tool run conan" if uv is found in PATH
        7. Falls back to ["conan"] (will fail if not available)
        """
        # 1. Explicit command from constructor
        if self._conan_cmd:
            return [self._conan_cmd]

        # 2-3. Environment variables
        for env_var in ("PCONS_CONAN", "CONAN"):
            env_val = os.environ.get(env_var)
            if env_val:
                return [env_val]

        # 4. Check if conan is in PATH
        if shutil.which("conan"):
            return ["conan"]

        # 5. Try uvx conan
        if shutil.which("uvx"):
            return ["uvx", "conan"]

        # 6. Try uv tool run conan
        if shutil.which("uv"):
            return ["uv", "tool", "run", "conan"]

        # 7. Fall back to conan (will fail in is_available)
        return ["conan"]

    def is_available(self) -> bool:
        """Check if conan is available."""
        cmd = self.conan_cmd
        # Check if the first command in the list exists
        return bool(shutil.which(cmd[0]))

    @property
    def profile_path(self) -> Path:
        """Path to the pcons-generated Conan profile."""
        return self.output_folder / "pcons-profile"

    @property
    def pkgconfig_dir(self) -> Path:
        """Path to the directory containing generated .pc files."""
        return self.output_folder

    def _run_conan(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        """Run conan with arguments.

        Args:
            *args: Arguments to pass to conan.
            check: If True, raise on non-zero exit code.

        Returns:
            CompletedProcess result.
        """
        cmd = [*self.conan_cmd, *args]
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=check,
        )

    def _detect_compiler_settings(
        self, toolchain: Toolchain | None = None
    ) -> dict[str, str]:
        """Detect compiler settings for Conan profile.

        Args:
            toolchain: Optional toolchain to get settings from.

        Returns:
            Dict of Conan settings.
        """
        settings: dict[str, str] = {}

        # Detect OS
        if self._platform.is_macos:
            settings["os"] = "Macos"
        elif self._platform.is_linux:
            settings["os"] = "Linux"
        elif self._platform.is_windows:
            settings["os"] = "Windows"

        # Detect architecture
        if self._platform.arch in ("arm64", "aarch64"):
            settings["arch"] = "armv8"
        elif self._platform.arch in ("x86_64", "amd64"):
            settings["arch"] = "x86_64"
        elif self._platform.arch in ("x86", "i686", "i386"):
            settings["arch"] = "x86"

        # Compiler settings from toolchain
        if toolchain is not None:
            compiler_name = toolchain.name.lower()
            if "gcc" in compiler_name:
                settings["compiler"] = "gcc"
                if hasattr(toolchain, "version") and toolchain.version:
                    # Extract major version
                    version_str = str(toolchain.version)
                    major = version_str.split(".")[0]
                    settings["compiler.version"] = major
                settings["compiler.libcxx"] = "libstdc++11"
            elif "clang" in compiler_name or "llvm" in compiler_name:
                if self._platform.is_macos:
                    settings["compiler"] = "apple-clang"
                else:
                    settings["compiler"] = "clang"
                if hasattr(toolchain, "version") and toolchain.version:
                    version_str = str(toolchain.version)
                    major = version_str.split(".")[0]
                    settings["compiler.version"] = major
                if self._platform.is_macos:
                    settings["compiler.libcxx"] = "libc++"
                else:
                    settings["compiler.libcxx"] = "libstdc++11"
            elif "msvc" in compiler_name:
                settings["compiler"] = "msvc"
        else:
            # Default to system compiler detection
            if self._platform.is_macos:
                settings["compiler"] = "apple-clang"
                settings["compiler.libcxx"] = "libc++"
            elif self._platform.is_linux:
                settings["compiler"] = "gcc"
                settings["compiler.libcxx"] = "libstdc++11"

        # Auto-detect compiler version if not set
        if "compiler.version" not in settings and "compiler" in settings:
            version = self._detect_compiler_version(settings.get("compiler", ""))
            if version:
                settings["compiler.version"] = version

        return settings

    def _detect_compiler_version(self, compiler: str) -> str | None:
        """Auto-detect compiler version by running the compiler.

        Args:
            compiler: Compiler name (gcc, clang, apple-clang, msvc).

        Returns:
            Major version string, or None if detection fails.
        """
        try:
            if compiler in ("apple-clang", "clang"):
                result = subprocess.run(
                    ["clang", "--version"],
                    capture_output=True,
                    text=True,
                )
                if result.returncode == 0:
                    # Parse "Apple clang version X.Y.Z" or "clang version X.Y.Z"
                    for line in result.stdout.split("\n"):
                        if "version" in line.lower():
                            parts = line.split()
                            for i, part in enumerate(parts):
                                if part == "version" and i + 1 < len(parts):
                                    return parts[i + 1].split(".")[0]
            elif compiler == "gcc":
                result = subprocess.run(
                    ["gcc", "--version"],
                    capture_output=True,
                    text=True,
                )
                if result.returncode == 0:
                    # Parse "gcc (Ubuntu X.Y.Z-...) X.Y.Z" or similar
                    line = result.stdout.split("\n")[0]
                    # Find version number pattern
                    import re

                    match = re.search(r"(\d+)\.\d+\.\d+", line)
                    if match:
                        return match.group(1)
        except (OSError, subprocess.SubprocessError):
            pass
        return None

    def set_profile_setting(self, key: str, value: str) -> None:
        """Set a custom profile setting.

        Args:
            key: Setting key (e.g., "compiler.cppstd").
            value: Setting value (e.g., "20").
        """
        self._profile_settings[key] = value

    def set_profile_conf(self, key: str, value: Any) -> None:
        """Set a custom profile conf value.

        Args:
            key: Conf key (e.g., "tools.build:cxxflags").
            value: Conf value (e.g., ["-Wall", "-Werror"]).
        """
        self._profile_conf[key] = value

    def sync_profile(
        self,
        toolchain: Toolchain | None = None,
        env: Any = None,
        build_type: str = "Release",
    ) -> Path:
        """Generate or update Conan profile from pcons settings.

        Args:
            toolchain: Toolchain to use for compiler settings.
            env: Environment for additional settings (optional).
            build_type: Build type (Release, Debug, etc.).

        Returns:
            Path to the generated profile.
        """
        # Ensure output folder exists
        self.output_folder.mkdir(parents=True, exist_ok=True)

        # Build profile content
        lines: list[str] = []
        lines.append("[settings]")

        # Get base settings from toolchain
        settings = self._detect_compiler_settings(toolchain)
        settings["build_type"] = build_type

        # Add custom settings
        settings.update(self._profile_settings)

        # Write settings
        for key, value in sorted(settings.items()):
            lines.append(f"{key}={value}")

        # Add buildenv section to set CC/CXX for CMake when building packages
        # This ensures Conan uses the same compiler as the toolchain
        buildenv: dict[str, str] = {}
        if toolchain is not None:
            # Get compiler commands from toolchain tools
            cc_cmd = self._get_toolchain_compiler_cmd(toolchain, "cc")
            cxx_cmd = self._get_toolchain_compiler_cmd(toolchain, "cxx")
            if cc_cmd:
                buildenv["CC"] = cc_cmd
            if cxx_cmd:
                buildenv["CXX"] = cxx_cmd

        if buildenv:
            lines.append("")
            lines.append("[buildenv]")
            for key, value in sorted(buildenv.items()):
                lines.append(f"{key}={value}")

        # Add conf section if there are conf values
        if self._profile_conf:
            lines.append("")
            lines.append("[conf]")
            for key, value in sorted(self._profile_conf.items()):
                if isinstance(value, list):
                    lines.append(f"{key}={json.dumps(value)}")
                else:
                    lines.append(f"{key}={value}")

        # Write profile
        profile_content = "\n".join(lines) + "\n"
        self.profile_path.write_text(profile_content)

        return self.profile_path

    def _get_toolchain_compiler_cmd(
        self, toolchain: Toolchain, tool_name: str
    ) -> str | None:
        """Get compiler command from toolchain.

        Args:
            toolchain: Toolchain to query.
            tool_name: Tool name ("cc" for C compiler, "cxx" for C++ compiler).

        Returns:
            Compiler command string, or None if not found.
        """
        # Try to get the tool from toolchain
        if hasattr(toolchain, "tools"):
            tools_dict = toolchain.tools
            if tool_name in tools_dict:
                tool = tools_dict[tool_name]
                # Get default vars which contain the cmd
                default_vars_method = getattr(tool, "default_vars", None)
                if callable(default_vars_method):
                    defaults = default_vars_method()
                    cmd = defaults.get("cmd")
                    if cmd:
                        # cmd could be a string or path-like
                        return str(cmd)
        return None

    def _compute_cache_key(self) -> str:
        """Compute a hash for caching purposes.

        The key is based on:
        - conanfile content
        - profile content
        - build_missing setting
        """
        hasher = hashlib.sha256()

        # Include conanfile content
        if self.conanfile.exists():
            hasher.update(self.conanfile.read_bytes())

        # Include profile content
        if self.profile_path.exists():
            hasher.update(self.profile_path.read_bytes())

        # Include build_missing
        hasher.update(str(self.build_missing).encode())

        return hasher.hexdigest()[:16]

    def _is_cache_valid(self) -> bool:
        """Check if cached install is still valid."""
        cache_key_file = self.output_folder / ".pcons_conan_cache_key"

        if not cache_key_file.exists():
            return False

        stored_key = cache_key_file.read_text().strip()
        current_key = self._compute_cache_key()

        return stored_key == current_key

    def _save_cache_key(self) -> None:
        """Save the current cache key."""
        cache_key_file = self.output_folder / ".pcons_conan_cache_key"
        cache_key_file.write_text(self._compute_cache_key())

    def install(
        self,
        conanfile: Path | None = None,
        force: bool = False,
    ) -> dict[str, PackageDescription]:
        """Run conan install and parse results.

        Args:
            conanfile: Override conanfile path.
            force: Force reinstall even if cache is valid.

        Returns:
            Dict mapping package names to PackageDescription objects.

        Raises:
            RuntimeError: If conan is not available or install fails.
        """
        if not self.is_available():
            raise RuntimeError(
                "Conan is not available. Please install it: pip install conan"
            )

        if conanfile is not None:
            self.conanfile = Path(conanfile)

        # Check if we need to run conan install
        if not force and self._is_cache_valid():
            return self._parse_pkgconfig_files()

        # Ensure profile exists
        if not self.profile_path.exists():
            self.sync_profile()

        # Ensure output folder exists
        self.output_folder.mkdir(parents=True, exist_ok=True)

        # Build conan install command
        # Use --profile:host and --profile:build to avoid requiring a default profile
        cmd: list[str] = [
            *self.conan_cmd,
            "install",
            str(self.conanfile.parent if self.conanfile.is_file() else self.conanfile),
            f"--profile:host={self.profile_path}",
            f"--profile:build={self.profile_path}",
            "-g",
            "PkgConfigDeps",
            f"--output-folder={self.output_folder}",
        ]

        if self.build_missing:
            cmd.append("--build=missing")

        # Run conan install
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            raise RuntimeError(
                f"Conan install failed:\n"
                f"Command: {' '.join(cmd)}\n"
                f"Stdout: {result.stdout}\n"
                f"Stderr: {result.stderr}"
            )

        # Save cache key
        self._save_cache_key()

        # Parse the generated .pc files
        return self._parse_pkgconfig_files()

    def _parse_pkgconfig_files(self) -> dict[str, PackageDescription]:
        """Parse all .pc files in the output folder.

        Automatically searches cmake_layout subfolders if no .pc files found
        in the main output folder.

        Returns:
            Dict mapping package names to PackageDescription objects.
        """
        packages: dict[str, PackageDescription] = {}

        # Find the directory containing .pc files
        # Conan with cmake_layout puts them in build/{build_type}/generators/
        pc_dir = self.output_folder
        if not list(pc_dir.glob("*.pc")):
            # Check cmake_layout subfolders
            for build_type in ["Release", "Debug", "RelWithDebInfo", "MinSizeRel"]:
                candidate = self.output_folder / "build" / build_type / "generators"
                if candidate.exists() and list(candidate.glob("*.pc")):
                    pc_dir = candidate
                    break

        if not list(pc_dir.glob("*.pc")):
            # No .pc files found anywhere
            self._packages = packages
            return packages

        # Create a PkgConfigFinder that searches in the pc_dir
        # Set PKG_CONFIG_PATH environment for pkg-config
        old_pkg_config_path = os.environ.get("PKG_CONFIG_PATH", "")
        try:
            # Prepend our pc_dir to PKG_CONFIG_PATH
            new_path = str(pc_dir)
            if old_pkg_config_path:
                new_path = f"{new_path}{os.pathsep}{old_pkg_config_path}"
            os.environ["PKG_CONFIG_PATH"] = new_path

            finder = PkgConfigFinder()
            if not finder.is_available():
                # Fallback to manual parsing if pkg-config is not available
                return self._parse_pc_files_manually()

            # Find all .pc files
            for pc_file in pc_dir.glob("*.pc"):
                pkg_name = pc_file.stem
                # Skip private files (those with - in name that are components)
                # but only if the base package also exists
                if "-" in pkg_name:
                    base_name = pkg_name.rsplit("-", 1)[0]
                    base_pc = pc_dir / f"{base_name}.pc"
                    if base_pc.exists():
                        continue  # Skip, this is a component

                pkg = finder.find(pkg_name)
                if pkg is not None:
                    pkg.found_by = "conan"
                    packages[pkg_name] = pkg

        finally:
            # Restore original PKG_CONFIG_PATH
            if old_pkg_config_path:
                os.environ["PKG_CONFIG_PATH"] = old_pkg_config_path
            elif "PKG_CONFIG_PATH" in os.environ:
                del os.environ["PKG_CONFIG_PATH"]

        self._packages = packages
        return packages

    def _parse_pc_files_manually(self) -> dict[str, PackageDescription]:
        """Parse .pc files manually when pkg-config is not available.

        This is a fallback that parses the basic structure of .pc files.

        Returns:
            Dict mapping package names to PackageDescription objects.
        """
        packages: dict[str, PackageDescription] = {}

        for pc_file in self.output_folder.glob("*.pc"):
            pkg = self._parse_single_pc_file(pc_file)
            if pkg is not None:
                packages[pkg.name] = pkg

        self._packages = packages
        return packages

    def _parse_single_pc_file(self, pc_file: Path) -> PackageDescription | None:
        """Parse a single .pc file.

        Args:
            pc_file: Path to the .pc file.

        Returns:
            PackageDescription or None if parsing fails.
        """
        try:
            content = pc_file.read_text()
        except OSError:
            return None

        variables: dict[str, str] = {}
        name = pc_file.stem
        version = ""
        cflags = ""
        libs = ""

        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            # Variable definition (e.g., prefix=/usr/local)
            if "=" in line and ":" not in line.split("=")[0]:
                key, _, value = line.partition("=")
                variables[key.strip()] = value.strip()

            # Field definition (e.g., Name: foo)
            elif ":" in line:
                key, _, value = line.partition(":")
                key = key.strip().lower()
                value = value.strip()

                if key == "name":
                    name = value
                elif key == "version":
                    version = value
                elif key == "cflags":
                    cflags = value
                elif key == "libs":
                    libs = value

        # Substitute variables recursively in cflags and libs
        def substitute_vars(s: str, max_iterations: int = 10) -> str:
            result = s
            for _ in range(max_iterations):
                old_result = result
                for var_name, var_value in variables.items():
                    result = result.replace(f"${{{var_name}}}", var_value)
                    result = result.replace(f"${var_name}", var_value)
                if result == old_result:
                    break  # No more substitutions possible
            return result

        cflags = substitute_vars(cflags)
        libs = substitute_vars(libs)

        # Parse cflags
        include_dirs: list[str] = []
        defines: list[str] = []
        compile_flags: list[str] = []

        for flag in cflags.split():
            if flag.startswith("-I"):
                include_dirs.append(flag[2:])
            elif flag.startswith("-D"):
                defines.append(flag[2:])
            else:
                compile_flags.append(flag)

        # Parse libs
        library_dirs: list[str] = []
        libraries: list[str] = []
        link_flags: list[str] = []

        for flag in libs.split():
            if flag.startswith("-L"):
                library_dirs.append(flag[2:])
            elif flag.startswith("-l"):
                libraries.append(flag[2:])
            else:
                link_flags.append(flag)

        return PackageDescription(
            name=name,
            version=version,
            include_dirs=include_dirs,
            library_dirs=library_dirs,
            libraries=libraries,
            defines=defines,
            compile_flags=compile_flags,
            link_flags=link_flags,
            found_by="conan",
        )

    def find(
        self,
        package_name: str,
        version: str | None = None,
        components: list[str] | None = None,
    ) -> PackageDescription | None:
        """Find a specific package from installed Conan packages.

        Note: This requires install() to have been called first.

        Args:
            package_name: Name of the package to find.
            version: Optional version requirement (not used for conan).
            components: Optional list of components (not implemented).

        Returns:
            PackageDescription if found, None otherwise.
        """
        if self._packages is None:
            # Try to parse existing .pc files
            if self.pkgconfig_dir.exists():
                self._parse_pkgconfig_files()
            else:
                return None

        return self._packages.get(package_name) if self._packages else None

    def __repr__(self) -> str:
        return (
            f"ConanFinder(conanfile={self.conanfile}, "
            f"output_folder={self.output_folder})"
        )
