# SPDX-License-Identifier: MIT
"""Variable management for pcons.

This module provides functions for managing variables passed via the command line or environment.
"""

from __future__ import annotations

import json
import os
from typing import overload

from pcons.core.cache import get_cache, reset_cache

# Internal storage for CLI variables (parsed PCONS_VARS for the current run)
_cli_vars: dict[str, str] | None = None


def _clear_cli_vars() -> None:
    """Clear cached CLI variables and the build-dir cache. Used for testing."""
    global _cli_vars
    _cli_vars = None
    reset_cache()


@overload
def get_var(name: str) -> str | None: ...


@overload
def get_var(name: str, default: str) -> str: ...


@overload
def get_var(name: str, default: None) -> str | None: ...


def get_var(name: str, default: str | None = None) -> str | None:
    """Get a build variable set on the command line or from environment.

    Variables can be set when invoking pcons:
        pcons PORT=ofx USE_CUDA=1

    In your pcons-build.py, access them with:
        port = get_var('PORT', default='ofx')
        use_cuda = get_var('USE_CUDA', default='0') == '1'

    Values configured on the command line persist across runs in the per-build-dir
    cache (CMakeCache-like), so a later bare `pcons configure` still sees them.

    Precedence (highest to lowest):
        1. Command line: pcons VAR=value  (this run)
        2. Environment variable: VAR=value pcons
        3. Persisted cache: value configured on the command line in a prior run
        4. default

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

    # Then OS environment
    env_value = os.environ.get(name)
    if env_value is not None:
        return env_value

    # Then the persisted cache. "vars" is public, guard against a non-dict.
    cached_vars = get_cache().get("vars")
    if isinstance(cached_vars, dict) and name in cached_vars:
        return str(cached_vars[name])

    return default


def get_variant(default: str = "release") -> str:
    """Get the build variant (debug, release, etc.).

    The variant can be set with:
        pcons --variant=debug

    Or when running directly:
        VARIANT=debug python pcons-build.py

    A variant chosen on the command line persists across runs in the per-build-dir
    cache, so a later bare `pcons configure` reuses it (like CMAKE_BUILD_TYPE).

    Precedence (highest to lowest):
        1. PCONS_VARIANT (set by pcons CLI)
        2. VARIANT environment variable
        3. Persisted cache from a prior configure
        4. default parameter

    Args:
        default: Default variant if not set.

    Returns:
        The variant name.
    """
    cached = get_cache().get("variant")
    cached_str = cached if isinstance(cached, str) else None
    return (
        os.environ.get("PCONS_VARIANT")
        or os.environ.get("VARIANT")
        or cached_str
        or default
    )
