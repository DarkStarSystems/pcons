# SPDX-License-Identifier: MIT
"""
Pcons: A Python-based build system that generates Ninja files.

Pcons is a modern build system inspired by SCons and CMake that uses Python
for build configuration and generates Ninja (or Makefile) build files.
"""

from __future__ import annotations

import json
import os

__version__ = "0.1.0-dev"

# Internal storage for CLI variables
_cli_vars: dict[str, str] | None = None


def get_var(name: str, default: str | None = None) -> str | None:
    """Get a build variable set on the command line or from environment.

    Variables can be set when invoking pcons:
        pcons PORT=ofx USE_CUDA=1

    In your build.py, access them with:
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
            except json.JSONDecodeError:
                _cli_vars = {}
        else:
            _cli_vars = {}

    # Check CLI vars first
    if name in _cli_vars:
        return _cli_vars[name]

    # Fall back to environment
    return os.environ.get(name, default)


def get_variant(default: str = "release") -> str:
    """Get the build variant (debug, release, etc.).

    The variant can be set with:
        pcons --variant=debug

    Or when running directly:
        VARIANT=debug python build.py

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


# Public API exports
__all__ = [
    "__version__",
    "get_var",
    "get_variant",
]
