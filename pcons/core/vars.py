# SPDX-License-Identifier: MIT
"""Variable management for pcons.

This module provides functions for managing variables passed via the command line or environment.
"""

from __future__ import annotations

import json
import os
from typing import overload

# Internal storage for CLI variables
_cli_vars: dict[str, str] | None = None


def _clear_cli_vars() -> None:
    """Clear the cached CLI variables. Used for testing purposes."""
    global _cli_vars
    _cli_vars = None


def _posix_specific_vars() -> dict[str, str]:
    """Get POSIX-specific variables to inject into the build environment."""
    return {
        "BINARY_EXT": "",
        "LIBRARY_EXT": ".so",
        "OBJECT_EXT": ".o",
        "ARCHIVE_EXT": ".a",
        "LIBRARY_PREFIX": "lib",
        "PATHSEP": ":",
        "LIBRARY_INSTALL_DIR": "lib",
        "ARCHIVE_INSTALL_DIR": "lib",
        "BINARY_INSTALL_DIR": "bin",
    }


def _platform_specific_vars() -> dict[str, str]:
    """Get platform-specific variables to inject into the build environment."""
    import platform

    vars = {}
    system = platform.system()
    if system == "Windows":
        vars["PLATFORM"] = "windows"
        vars["BINARY_EXT"] = ".exe"
        vars["LIBRARY_EXT"] = ".dll"
        vars["OBJECT_EXT"] = ".obj"
        vars["LIBRARY_EXT"] = ".dll"
        vars["ARCHIVE_EXT"] = ".lib"
        vars["LIBRARY_PREFIX"] = ""
        vars["PATHSEP"] = ";"
        vars["LIBRARY_INSTALL_DIR"] = "bin"
        vars["ARCHIVE_INSTALL_DIR"] = "lib"
        vars["BINARY_INSTALL_DIR"] = "bin"
    elif system == "Darwin":
        vars.update(_posix_specific_vars())
        vars["PLATFORM"] = "macos"
        vars["LIBRARY_EXT"] = ".dylib"
    elif system == "Linux":
        vars.update(_posix_specific_vars())
        vars["PLATFORM"] = "linux"
    else:
        # Fallback to UNIX-style for unknown platforms
        vars["PLATFORM"] = system.lower()
        vars.update(_posix_specific_vars())

    vars["HOST_ARCH"] = platform.machine()
    vars["HOST_OS"] = system.lower()

    # apply environment overrides for platform vars
    for key in vars.keys():
        env_value = os.environ.get(key)
        if env_value is not None:
            vars[key] = env_value

    return vars


_platform_vars = _platform_specific_vars()

def _reload_platform_vars() -> None:
    """Reload platform-specific variables. Used for testing purposes."""
    global _platform_vars
    _platform_vars = _platform_specific_vars()

def get_cli_var(name: str, default: str | None = None) -> str | None:
    """Get a build variable set on the command line or from environment.

    Variables can be set when invoking pcons:
        pcons PORT=ofx USE_CUDA=1

    In your pcons-build.py, access them with:
        port = get_var('PORT', default='ofx')
        use_cuda = get_var('USE_CUDA', default='0') == '1'

    Precedence (highest to lowest):
        1. Command line: pcons VAR=value
        2. Environment variable: VAR=value pcons

    Args:
        name: Variable name.
        default: Default value if not set.

    Returns:
        The variable value, or default if not set.
    """
    global _cli_vars

    # Lazy-load CLI vars from environment on first access
    if _cli_vars is None:
        pcons_vars = os.environ.get("PCONS_VARS")
        if pcons_vars:
            try:
                _cli_vars = json.loads(pcons_vars)
            except json.JSONDecodeError as e:  # noqa: F821
                import warnings

                warnings.warn(
                    f"PCONS_VARS environment variable contains invalid JSON: {e}. "
                    "All CLI variable overrides will be ignored.",
                    stacklevel=2,
                )
                _cli_vars = {}
        else:
            _cli_vars = {}

    # Check CLI vars first
    assert _cli_vars is not None
    if name in _cli_vars:
        return _cli_vars[name]

    # Fall back to environment
    return os.environ.get(name, default)


class _Missing:
    pass


_MISSING = _Missing()


@overload
def get_var(name: str) -> str: ...


@overload
def get_var(name: str, default: None) -> str | None: ...


@overload
def get_var(name: str, default: str) -> str: ...


def get_var(name: str, default: str | None | _Missing = _MISSING) -> str | None:
    """Get a build variable set on the command line, environment, or platform specific defaults.

    Raises ValueError if the variable is not found and no default is provided.
    """
    cli_value = get_cli_var(name)
    if cli_value is not None:
        return cli_value
    value = _platform_vars.get(name, _MISSING)
    if isinstance(value, _Missing):
        if isinstance(default, _Missing):
            raise ValueError(f"Build variable {name!r} is not set")
        return default  # type: ignore[return-value]  # narrowed: str | None
    assert isinstance(value, str)
    return value


def get_variant(default: str = "release") -> str:
    """Get the build variant (debug, release, etc.).

    The variant can be set with:
        pcons --variant=debug

    Or when running directly:
        VARIANT=debug python pcons-build.py

    Precedence (highest to lowest):
        1. PCONS_VARIANT (set by pcons CLI)
        2. VARIANT environment variable
        3. default parameter

    Args:
        default: Default variant if not set.

    Returns:
        The variant name.
    """
    return os.environ.get("PCONS_VARIANT") or os.environ.get("VARIANT") or default
