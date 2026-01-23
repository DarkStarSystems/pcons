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
from pcons.core.debug import trace, trace_value

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
        trace("configure", "Finding program: %s", name)

        # Check cache first
        cache_key = f"program:{name}"
        if cache_key in self._cache:
            cached = self._cache[cache_key]
            path = Path(cached["path"])
            if path.exists():
                trace("configure", "  Found in cache: %s", path)
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

    # Feature check methods that track results for config header generation

    def define(self, name: str, value: str | int | bool = 1) -> None:
        """Define a preprocessor macro for the config header.

        This adds a #define to the config header. Use this when you
        know a feature is present without needing to check.

        Args:
            name: Macro name (e.g., "HAVE_FEATURE_X").
            value: Macro value (1 for feature flags, or an integer/string).

        Example:
            config.define("VERSION_MAJOR", 1)
            config.define("VERSION_MINOR", 2)
            config.define("HAVE_CUSTOM_FEATURE")
        """
        defines = self._cache.setdefault("_defines", {})
        if isinstance(value, bool):
            defines[name] = 1 if value else None  # None means #undef
        else:
            defines[name] = value

    def undefine(self, name: str) -> None:
        """Mark a macro as undefined for the config header.

        This adds a /* #undef NAME */ comment to the config header.

        Args:
            name: Macro name.
        """
        defines = self._cache.setdefault("_defines", {})
        defines[name] = None

    def check_header(
        self,
        header: str,
        *,
        define_name: str | None = None,
        lang: str = "c",
    ) -> bool:
        """Check if a header file exists and can be included.

        If found, defines HAVE_<HEADER>_H (with dots and slashes replaced).

        Args:
            header: Header file name (e.g., "stdint.h", "sys/types.h").
            define_name: Override for the define name.
            lang: Language ('c' or 'cxx').

        Returns:
            True if header is available.

        Example:
            if config.check_header("stdint.h"):
                # HAVE_STDINT_H is defined
                pass
        """
        # Cache key for this check
        cache_key = f"header:{header}"
        if cache_key in self._cache:
            result = bool(self._cache[cache_key])
        else:
            # Try to compile a simple include
            source = f"#include <{header}>\nint main(void) {{ return 0; }}\n"
            result = self.check_compile(source, lang=lang)
            self._cache[cache_key] = result

        # Generate define name
        if define_name is None:
            # HAVE_STDINT_H, HAVE_SYS_TYPES_H, etc.
            safe_name = header.upper().replace(".", "_").replace("/", "_")
            define_name = f"HAVE_{safe_name}"

        # Record the result
        if result:
            self.define(define_name)
        else:
            self.undefine(define_name)

        return result

    def check_sizeof(
        self,
        type_name: str,
        *,
        define_name: str | None = None,
        headers: list[str] | None = None,
        default: int | None = None,
    ) -> int | None:
        """Check the size of a type.

        Defines SIZEOF_<TYPE> with the size in bytes.

        Args:
            type_name: C type name (e.g., "int", "void*", "long long").
            define_name: Override for the define name.
            headers: Headers to include before checking.
            default: Default value if check fails.

        Returns:
            Size in bytes, or default if check fails.

        Example:
            int_size = config.check_sizeof("int")  # Defines SIZEOF_INT
            ptr_size = config.check_sizeof("void*")  # Defines SIZEOF_VOIDP
        """
        cache_key = f"sizeof:{type_name}"
        if cache_key in self._cache:
            cached = self._cache[cache_key]
            size: int | None = int(cached) if cached is not None else None
        else:
            # For now, use ctypes to get sizes for common types
            # Real implementation would compile and run a test program
            size = self._get_sizeof_ctypes(type_name)
            if size is None:
                size = default
            self._cache[cache_key] = size

        # Generate define name
        if define_name is None:
            safe_name = type_name.upper().replace(" ", "_").replace("*", "P")
            define_name = f"SIZEOF_{safe_name}"

        if size is not None:
            self.define(define_name, size)

        return size

    def _get_sizeof_ctypes(self, type_name: str) -> int | None:
        """Get size of a type using ctypes (fallback method)."""
        import ctypes

        # Map of type names to their sizes
        # We use sizeof directly with the type object
        size_map: dict[str, int] = {
            "char": ctypes.sizeof(ctypes.c_char),
            "short": ctypes.sizeof(ctypes.c_short),
            "int": ctypes.sizeof(ctypes.c_int),
            "long": ctypes.sizeof(ctypes.c_long),
            "long long": ctypes.sizeof(ctypes.c_longlong),
            "float": ctypes.sizeof(ctypes.c_float),
            "double": ctypes.sizeof(ctypes.c_double),
            "void*": ctypes.sizeof(ctypes.c_void_p),
            "size_t": ctypes.sizeof(ctypes.c_size_t),
            "ssize_t": ctypes.sizeof(ctypes.c_ssize_t),
        }

        return size_map.get(type_name.lower())

    def check_symbol(
        self,
        symbol: str,
        *,
        header: str | None = None,
        define_name: str | None = None,
        lang: str = "c",
    ) -> bool:
        """Check if a symbol (function, variable, macro) exists.

        Defines HAVE_<SYMBOL> if the symbol exists.

        Args:
            symbol: Symbol name (e.g., "pthread_create", "M_PI").
            header: Header file that declares the symbol.
            define_name: Override for the define name.
            lang: Language ('c' or 'cxx').

        Returns:
            True if symbol exists.

        Example:
            if config.check_symbol("pthread_create", header="pthread.h"):
                # HAVE_PTHREAD_CREATE is defined
                pass
        """
        cache_key = f"symbol:{symbol}"
        if cache_key in self._cache:
            result = bool(self._cache[cache_key])
        else:
            # Build test source
            include = f"#include <{header}>\n" if header else ""
            # Try different approaches to detect the symbol
            # First try using it as a function pointer (works for functions)
            source = f"""{include}
int main(void) {{
    void (*fp)(void) = (void (*)(void)){symbol};
    (void)fp;
    return 0;
}}
"""
            result = self.check_compile(source, lang=lang)
            self._cache[cache_key] = result

        # Generate define name
        if define_name is None:
            safe_name = symbol.upper()
            define_name = f"HAVE_{safe_name}"

        if result:
            self.define(define_name)
        else:
            self.undefine(define_name)

        return result

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
            config.check_header("stdint.h")
            config.check_sizeof("int")
            config.define("VERSION", "1.0.0")
            config.write_config_header("config.h")

        Generated header:
            #ifndef CONFIG_H
            #define CONFIG_H

            /* Platform detection */
            #define PCONS_OS_DARWIN 1
            #define PCONS_ARCH_ARM64 1

            /* Header checks */
            #define HAVE_STDINT_H 1

            /* Type sizes */
            #define SIZEOF_INT 4

            /* Custom definitions */
            #define VERSION "1.0.0"

            #endif /* CONFIG_H */
        """
        path = Path(path)

        # Generate include guard
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

        # Collect defines by category
        defines = self._cache.get("_defines", {})

        # Separate into categories
        have_defs = {k: v for k, v in defines.items() if k.startswith("HAVE_")}
        sizeof_defs = {k: v for k, v in defines.items() if k.startswith("SIZEOF_")}
        other_defs = {
            k: v
            for k, v in defines.items()
            if not k.startswith("HAVE_") and not k.startswith("SIZEOF_")
        }

        # Write header checks
        if have_defs:
            lines.append("/* Feature and header checks */")
            for name in sorted(have_defs.keys()):
                value = have_defs[name]
                if value is None:
                    lines.append(f"/* #undef {name} */")
                else:
                    lines.append(f"#define {name} {value}")
            lines.append("")

        # Write sizeof checks
        if sizeof_defs:
            lines.append("/* Type sizes */")
            for name in sorted(sizeof_defs.keys()):
                value = sizeof_defs[name]
                if value is not None:
                    lines.append(f"#define {name} {value}")
            lines.append("")

        # Write other definitions
        if other_defs:
            lines.append("/* Custom definitions */")
            for name in sorted(other_defs.keys()):
                value = other_defs[name]
                if value is None:
                    lines.append(f"/* #undef {name} */")
                elif isinstance(value, str):
                    # Quote string values
                    lines.append(f'#define {name} "{value}"')
                else:
                    lines.append(f"#define {name} {value}")
            lines.append("")

        lines.append(f"#endif /* {guard} */")
        lines.append("")

        # Write the file
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines))

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
