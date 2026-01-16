# SPDX-License-Identifier: MIT
"""Configure context for pcons.

The Configure class provides the context for the configure phase,
including tool detection, feature checks, and configuration caching.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pcons.configure.platform import get_platform

if TYPE_CHECKING:
    from pcons.tools.toolchain import Toolchain


@dataclass
class ProgramInfo:
    """Information about a found program.

    Attributes:
        path: Path to the program executable.
        version: Version string if detected.
    """

    path: Path
    version: str | None = None


class Configure:
    """Context for the configure phase.

    The Configure class manages:
    - Platform detection
    - Program/tool discovery
    - Feature checks
    - Configuration caching

    Example:
        config = Configure(build_dir=Path("build"))

        # Find a program
        gcc = config.find_program("gcc")
        if gcc:
            print(f"Found gcc at {gcc.path}")

        # Check for a toolchain
        toolchain = config.find_toolchain("gcc")

        # Save configuration for later
        config.save()

    Attributes:
        platform: The detected platform.
        build_dir: Directory for build outputs and cache.
    """

    def __init__(
        self,
        *,
        build_dir: Path | str = "build",
        cache_file: str = "pcons_config.json",
    ) -> None:
        """Create a configure context.

        Args:
            build_dir: Directory for build outputs.
            cache_file: Name of the cache file within build_dir.
        """
        self.platform = get_platform()
        self.build_dir = Path(build_dir)
        self._cache_file = cache_file
        self._cache: dict[str, Any] = {}
        self._toolchains: dict[str, Toolchain] = {}
        self._programs: dict[str, ProgramInfo] = {}

        # Try to load existing cache
        self._load_cache()

    def _cache_path(self) -> Path:
        """Get the path to the cache file."""
        return self.build_dir / self._cache_file

    def _load_cache(self) -> None:
        """Load configuration from cache file if it exists."""
        cache_path = self._cache_path()
        if cache_path.exists():
            try:
                with open(cache_path) as f:
                    self._cache = json.load(f)
            except (json.JSONDecodeError, OSError):
                self._cache = {}

    def save(self, path: Path | None = None) -> None:
        """Save configuration to cache file.

        Args:
            path: Optional path override for cache file.
        """
        cache_path = path or self._cache_path()
        cache_path.parent.mkdir(parents=True, exist_ok=True)

        with open(cache_path, "w") as f:
            json.dump(self._cache, f, indent=2, default=str)
            f.write("\n")

    def set(self, key: str, value: Any) -> None:
        """Set a configuration value.

        Args:
            key: Configuration key.
            value: Value to store.
        """
        self._cache[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        """Get a configuration value.

        Args:
            key: Configuration key.
            default: Default value if not found.

        Returns:
            The configured value or default.
        """
        return self._cache.get(key, default)

    def find_program(
        self,
        name: str,
        *,
        hints: list[Path | str] | None = None,
        version_flag: str = "--version",
        required: bool = False,
    ) -> ProgramInfo | None:
        """Find a program on the system.

        Searches for the program in:
        1. Hint paths (if provided)
        2. PATH environment variable

        Args:
            name: Program name (e.g., 'gcc', 'python3').
            hints: Additional paths to search.
            version_flag: Flag to get version (for version detection).
            required: If True, raise error if not found.

        Returns:
            ProgramInfo if found, None otherwise.

        Raises:
            FileNotFoundError: If required and not found.
        """
        # Check cache first
        cache_key = f"program:{name}"
        if cache_key in self._cache:
            cached = self._cache[cache_key]
            path = Path(cached["path"])
            if path.exists():
                return ProgramInfo(path=path, version=cached.get("version"))

        # Search for the program
        found_path: Path | None = None

        # Check hints first
        if hints:
            for hint in hints:
                hint_path = Path(hint)
                if hint_path.is_file() and os.access(hint_path, os.X_OK):
                    found_path = hint_path
                    break
                # Check if hint is a directory containing the program
                candidate = hint_path / name
                if self.platform.is_windows and not candidate.suffix:
                    candidate = candidate.with_suffix(".exe")
                if candidate.is_file() and os.access(candidate, os.X_OK):
                    found_path = candidate
                    break

        # Search PATH
        if found_path is None:
            found_path = self._which(name)

        if found_path is None:
            if required:
                raise FileNotFoundError(f"Required program not found: {name}")
            return None

        # Try to get version
        version = self._get_program_version(found_path, version_flag)

        # Cache the result
        self._cache[cache_key] = {
            "path": str(found_path),
            "version": version,
        }

        info = ProgramInfo(path=found_path, version=version)
        self._programs[name] = info
        return info

    def _which(self, name: str) -> Path | None:
        """Find a program in PATH using shutil.which."""
        result = shutil.which(name)
        if result:
            return Path(result)
        return None

    def _get_program_version(self, path: Path, version_flag: str) -> str | None:
        """Try to get the version of a program."""
        try:
            result = subprocess.run(
                [str(path), version_flag],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                # Return first non-empty line
                for line in result.stdout.split("\n"):
                    line = line.strip()
                    if line:
                        return line
            return None
        except (subprocess.TimeoutExpired, OSError):
            return None

    def find_toolchain(
        self,
        kind: str,
        *,
        candidates: list[str] | None = None,
    ) -> Toolchain | None:
        """Find and configure a toolchain.

        Args:
            kind: Toolchain kind (e.g., 'gcc', 'llvm', 'msvc').
            candidates: Optional list of toolchain names to try.

        Returns:
            Configured toolchain if found, None otherwise.
        """
        # Check if already configured
        if kind in self._toolchains:
            return self._toolchains[kind]

        # Import toolchain classes dynamically to avoid circular imports
        toolchain = self._create_toolchain(kind)
        if toolchain is None:
            return None

        # Try to configure
        if toolchain.configure(self):
            self._toolchains[kind] = toolchain
            return toolchain

        return None

    def _create_toolchain(self, kind: str) -> Toolchain | None:
        """Create a toolchain instance by kind.

        This is a factory method that will be extended as
        toolchains are implemented.
        """
        # Toolchains will be registered here as they're implemented
        # For now, return None
        return None

    def register_toolchain(self, toolchain: Toolchain) -> None:
        """Register a pre-configured toolchain.

        Args:
            toolchain: Toolchain to register.
        """
        self._toolchains[toolchain.name] = toolchain

    def check_compile(
        self,
        source: str,
        *,
        lang: str = "c",
        flags: list[str] | None = None,
    ) -> bool:
        """Check if source code compiles.

        Args:
            source: Source code to compile.
            lang: Language ('c' or 'cxx').
            flags: Additional compiler flags.

        Returns:
            True if compilation succeeds.
        """
        # This is a placeholder - real implementation needs a compiler
        # Will be implemented when toolchains are available
        return False

    def check_link(
        self,
        source: str,
        *,
        lang: str = "c",
        flags: list[str] | None = None,
        libs: list[str] | None = None,
    ) -> bool:
        """Check if source code compiles and links.

        Args:
            source: Source code to compile.
            lang: Language ('c' or 'cxx').
            flags: Additional compiler flags.
            libs: Libraries to link.

        Returns:
            True if compilation and linking succeed.
        """
        # Placeholder - needs compiler/linker
        return False

    def __repr__(self) -> str:
        return (
            f"Configure(platform={self.platform.os}/{self.platform.arch}, "
            f"build_dir={self.build_dir})"
        )


def load_config(path: Path | str = "build/pcons_config.json") -> dict[str, Any]:
    """Load a saved configuration.

    Args:
        path: Path to the config file.

    Returns:
        Configuration dict.

    Raises:
        FileNotFoundError: If config file doesn't exist.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with open(path) as f:
        data: dict[str, Any] = json.load(f)
        return data
