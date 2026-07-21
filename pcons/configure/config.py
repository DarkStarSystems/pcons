# SPDX-License-Identifier: MIT
"""Configure context for pcons.

The Configure class provides the context for the configure phase,
including tool detection, feature checks, and configuration caching.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pcons.configure.checks import cache_signature
from pcons.configure.platform import get_platform
from pcons.core.debug import trace, trace_value

if TYPE_CHECKING:
    from pcons.core.environment import Environment
    from pcons.tools.toolchain import Toolchain

logger = logging.getLogger(__name__)


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
        # Derived output state for the config header. Rebuilt fresh from the
        # define()/undefine()/check_*() calls made *this run* — never loaded
        # from or persisted to the cache, so a removed define() call actually
        # stops emitting once the build script no longer calls it.
        self._defines: dict[str, str | int | None] = {}

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
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(
                    "Corrupt or unreadable cache %s: %s — re-configuring", cache_path, e
                )
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
        trace("configure", "Finding program: %s", name)

        # Key the cache by a PATH signature so a changed PATH (different
        # dev shell, added SDK bin dir) re-searches instead of returning a
        # result found under a different environment.
        path_sig = cache_signature(os.environ.get("PATH", ""))
        cache_key = f"program:{name}:{path_sig}"

        # Explicit hints take priority over both the cache and PATH.
        found_path: Path | None = None
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

        # No hint match: fall back to the cache.
        if found_path is None and cache_key in self._cache:
            cached = self._cache[cache_key]
            path = Path(cached["path"])
            if path.exists():
                trace("configure", "  Found in cache: %s", path)
                return ProgramInfo(path=path, version=cached.get("version"))

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
        trace("configure", "  Found: %s", found_path)
        trace_value("configure", "version", version)
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

    # Compile probes (check_header etc.) need an Environment and a tool,
    # which Configure doesn't have — they live in ToolChecks
    # (pcons.configure.checks); record outcomes here via define()/undefine().

    def define(self, name: str, value: str | int | bool = 1) -> None:
        """Add a #define to the config header (no check needed).

        Args:
            name: Macro name (e.g., "HAVE_FEATURE_X").
            value: Macro value (1 for feature flags, or an integer/string).

        Example:
            config.define("VERSION_MAJOR", 1)
            config.define("VERSION_MINOR", 2)
            config.define("HAVE_CUSTOM_FEATURE")
        """
        if isinstance(value, bool):
            self._defines[name] = 1 if value else None  # None means #undef
        else:
            self._defines[name] = value

    def undefine(self, name: str) -> None:
        """Mark a macro as undefined (/* #undef NAME */) in the config header."""
        self._defines[name] = None

    def check_sizeof(
        self,
        type_name: str,
        *,
        env: Environment,
        tool: str = "cc",
        define_name: str | None = None,
        headers: list[str] | None = None,
        default: int | None = None,
    ) -> int | None:
        """Check the size of a type with the configured compiler.

        Defines SIZEOF_<TYPE> with the size in bytes. Uses a compile-time
        probe against the *target* compiler — no code is executed, so it
        is correct under cross-compilation.

        Args:
            type_name: C type name (e.g., "int", "void*", "long long").
            env: Environment whose configured compiler answers the check.
            tool: Tool to compile with ("cc" or "cxx").
            define_name: Override for the define name.
            headers: Headers to include before checking.
            default: Default value if the check fails.

        Returns:
            Size in bytes, or default if the check fails.

        Example:
            int_size = config.check_sizeof("int", env=env)  # SIZEOF_INT
            ptr_size = config.check_sizeof("void*", env=env)  # SIZEOF_VOIDP
        """
        from pcons.configure.checks import ToolChecks

        size = ToolChecks(self, env, tool).check_type_size(type_name, headers=headers)
        if size is None:
            size = default

        if define_name is None:
            safe_name = type_name.upper().replace(" ", "_").replace("*", "P")
            define_name = f"SIZEOF_{safe_name}"

        if size is not None:
            self.define(define_name, size)

        return size

    def write_config_header(
        self,
        path: Path | str,
        *,
        guard: str | None = None,
        include_platform: bool = True,
    ) -> None:
        """Write a C/C++ configuration header file.

        Generates a header with #define statements for all detected
        features, sizes, and custom definitions.

        Args:
            path: Path to write the header file.
            guard: Include guard name (default: derived from filename).
            include_platform: Include platform detection macros.

        Example:
            checks = ToolChecks(config, env, "cc")
            if checks.check_header("stdint.h").success:
                config.define("HAVE_STDINT_H")
            config.check_sizeof("int")
            config.define("VERSION", "1.0.0")
            config.write_config_header("config.h")
        """
        path = Path(path)

        if guard is None:
            guard = path.name.upper().replace(".", "_").replace("-", "_")

        lines: list[str] = []
        lines.append(f"#ifndef {guard}")
        lines.append(f"#define {guard}")
        lines.append("")
        lines.append("/* Generated by pcons configure */")
        lines.append("")

        # Platform detection
        if include_platform:
            lines.append("/* Platform detection */")
            os_name = self.platform.os.upper()
            arch_name = self.platform.arch.upper().replace("-", "_")
            lines.append(f"#define PCONS_OS_{os_name} 1")
            lines.append(f"#define PCONS_ARCH_{arch_name} 1")
            if self.platform.is_64bit:
                lines.append("#define PCONS_64BIT 1")
            lines.append("")

        # Group defines by category
        defines = self._defines
        have_defs = {k: v for k, v in defines.items() if k.startswith("HAVE_")}
        sizeof_defs = {k: v for k, v in defines.items() if k.startswith("SIZEOF_")}
        other_defs = {
            k: v
            for k, v in defines.items()
            if not k.startswith("HAVE_") and not k.startswith("SIZEOF_")
        }

        if have_defs:
            lines.append("/* Feature and header checks */")
            for name in sorted(have_defs.keys()):
                value = have_defs[name]
                if value is None:
                    lines.append(f"/* #undef {name} */")
                else:
                    lines.append(f"#define {name} {value}")
            lines.append("")

        if sizeof_defs:
            lines.append("/* Type sizes */")
            for name in sorted(sizeof_defs.keys()):
                value = sizeof_defs[name]
                if value is not None:
                    lines.append(f"#define {name} {value}")
            lines.append("")

        if other_defs:
            lines.append("/* Custom definitions */")
            for name in sorted(other_defs.keys()):
                value = other_defs[name]
                if value is None:
                    lines.append(f"/* #undef {name} */")
                elif isinstance(value, str):
                    lines.append(f'#define {name} "{value}"')
                else:
                    lines.append(f"#define {name} {value}")
            lines.append("")

        lines.append(f"#endif /* {guard} */")
        lines.append("")

        # Write-if-changed: don't bump the mtime (and rebuild everything
        # that includes it) when content is unchanged.
        content = "\n".join(lines)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.read_text() == content:
            return
        path.write_text(content)

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
