# SPDX-License-Identifier: MIT
"""Variable management for pcons.

This module provides functions for managing variables passed via the command line or environment.
"""

from __future__ import annotations

import builtins
import json
import os
from pathlib import Path, PurePath
from typing import TypeAlias, overload

from pcons.core.cache import reset_cache
from pcons.core.errors import ConfigureError

# Types get_var can convert a raw variable string into.
VarValue: TypeAlias = bool | int | float | str | Path

_TRUE_VALUES = frozenset({"1", "on", "yes", "true", "y"})
_FALSE_VALUES = frozenset({"0", "off", "no", "false", "n"})
_SUPPORTED_TYPES: tuple[builtins.type[VarValue], ...] = (bool, int, float, str, Path)
_SUPPORTED_NAMES = ", ".join(t.__name__ for t in _SUPPORTED_TYPES)

# Internal storage for CLI variables (parsed PCONS_VARS for the current run)
_cli_vars: dict[str, str] | None = None

# Names passed to get_var this run, so the CLI can warn about persisted vars the
# build script never reads (a typo like `pcons FEATRUE=on`).
_accessed_vars: set[str] = set()


def _clear_cli_vars() -> None:
    """Clear cached CLI variables and the build-dir cache. Used for testing."""
    global _cli_vars
    _cli_vars = None
    _accessed_vars.clear()
    reset_cache()


def _accessed_var_names() -> set[str]:
    """Return the variable names get_var has been called with this run."""
    return set(_accessed_vars)


def _var_type_of(candidate: builtins.type[object]) -> builtins.type[VarValue] | None:
    """Map a class to the conversion it selects, or None if unsupported.

    ``Path("/x")`` is a PosixPath or a WindowsPath, so a Path default would
    otherwise select a per-platform class no caller can name portably.
    """
    if issubclass(candidate, PurePath):
        return Path
    for supported in _SUPPORTED_TYPES:
        if candidate is supported:
            return supported
    return None


def _resolve_var_type(
    name: str,
    default: VarValue | None,
    requested: builtins.type[VarValue] | None,
) -> builtins.type[VarValue]:
    """Pick the type a variable's raw string should be converted to."""
    if requested is not None:
        target = _var_type_of(requested)
        if target is None:
            raise ConfigureError(
                f"get_var({name!r}): unsupported type={requested!r}; "
                f"expected {_SUPPORTED_NAMES}"
            )
        if default is not None and _var_type_of(builtins.type(default)) is not target:
            raise ConfigureError(
                f"get_var({name!r}): default {default!r} is "
                f"{builtins.type(default).__name__}, which conflicts with "
                f"type={requested.__name__}"
            )
        return target

    if default is None:
        return str

    inferred = _var_type_of(builtins.type(default))
    if inferred is None:
        raise ConfigureError(
            f"get_var({name!r}): default {default!r} is a "
            f"{builtins.type(default).__name__}; expected {_SUPPORTED_NAMES}"
        )
    return inferred


def _coerce_var(name: str, raw: str, target: builtins.type[VarValue]) -> VarValue:
    """Convert a raw variable string to ``target``, or raise."""
    text = raw.strip()
    if target is Path:
        if not text:
            raise ConfigureError(f"{name}={raw!r} is not a valid path; it is empty")
        return Path(text)
    if target is bool:
        lowered = text.lower()
        if lowered in _TRUE_VALUES:
            return True
        if lowered in _FALSE_VALUES:
            return False
        raise ConfigureError(
            f"{name}={raw!r} is not a boolean; expected one of "
            f"{', '.join(sorted(_TRUE_VALUES))} (true) or "
            f"{', '.join(sorted(_FALSE_VALUES))} (false)"
        )
    try:
        return target(text)  # type: ignore[call-arg]
    except ValueError as e:
        raise ConfigureError(f"{name}={raw!r} is not a valid {target.__name__}") from e


def _raw_var(name: str) -> str | None:
    """Return a variable's raw string from PCONS_VARS or the environment."""
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
    return os.environ.get(name)


@overload
def get_var(name: str) -> str | None: ...


@overload
def get_var(
    name: str, default: bool, *, type: builtins.type[bool] | None = None
) -> bool: ...


@overload
def get_var(
    name: str, default: int, *, type: builtins.type[int] | None = None
) -> int: ...


@overload
def get_var(
    name: str, default: float, *, type: builtins.type[float] | None = None
) -> float: ...


@overload
def get_var(
    name: str, default: str, *, type: builtins.type[str] | None = None
) -> str: ...


@overload
def get_var(
    name: str, default: Path, *, type: builtins.type[Path] | None = None
) -> Path: ...


@overload
def get_var(
    name: str, default: None = None, *, type: builtins.type[bool]
) -> bool | None: ...


@overload
def get_var(
    name: str, default: None = None, *, type: builtins.type[int]
) -> int | None: ...


@overload
def get_var(
    name: str, default: None = None, *, type: builtins.type[float]
) -> float | None: ...


@overload
def get_var(
    name: str, default: None = None, *, type: builtins.type[str]
) -> str | None: ...


@overload
def get_var(
    name: str, default: None = None, *, type: builtins.type[Path]
) -> Path | None: ...


@overload
def get_var(name: str, default: None) -> str | None: ...


def get_var(
    name: str,
    default: VarValue | None = None,
    *,
    type: builtins.type[VarValue] | None = None,
) -> VarValue | None:
    """Get a build variable set on the command line or from environment.

    Variables can be set when invoking pcons:
        pcons PORT=ofx USE_CUDA=1

    In your pcons-build.py, access them with:
        port = get_var('PORT', 'ofx')
        use_cuda = get_var('USE_CUDA', False)
        opt_level = get_var('OPT_LEVEL', 2)
        prefix = get_var('PREFIX', Path('/usr/local'))

    The default's type drives the conversion, so `get_var('X', False)` returns a
    bool and `get_var('X', 2)` returns an int. Pass `type=` when there is no
    default: `get_var('BUILD_TESTS', type=bool)` returns None when unset. With no
    default and no `type=`, the raw string is returned, as before.

    Booleans accept 1/on/yes/true/y and 0/off/no/false/n, case-insensitive; any
    other value raises rather than reading as false. A Path is taken verbatim,
    not resolved, so a relative value stays relative to whatever the caller
    resolves it against. The default itself is never parsed, it is returned
    as-is when the variable is unset.

    Values configured on the command line persist across runs: the CLI folds a
    prior configure's cached vars into PCONS_VARS before the script runs, so a
    later bare `pcons configure` still sees them (CMakeCache-like). This reader
    consults only PCONS_VARS and the environment; the cache never appears here.

    Precedence (highest to lowest):
        1. Command line: pcons VAR=value  (this run, via PCONS_VARS)
        2. Environment variable: VAR=value pcons
        3. default

    Args:
        name: Variable name.
        default: Default value if not set. Its type selects the conversion.
        type: Explicit conversion type (bool, int, float, str or Path). Must
            agree with the default's type when both are given.

    Returns:
        The variable value converted to the requested type, or default if not set.

    Raises:
        ConfigureError: The value cannot be converted, or type and default disagree.
    """
    _accessed_vars.add(name)

    target = _resolve_var_type(name, default, type)

    raw = _raw_var(name)
    if raw is None:
        return default
    if target is str:
        return raw
    return _coerce_var(name, raw, target)


def get_variant(default: str = "release") -> str:
    """Get the build variant (debug, release, etc.).

    The variant can be set with:
        pcons --variant=debug

    Or when running directly:
        VARIANT=debug python pcons-build.py

    A variant chosen on the command line persists across runs: the CLI folds a
    prior configure's cached variant into PCONS_VARIANT before the script runs,
    so a later bare `pcons configure` reuses it (like CMAKE_BUILD_TYPE). This
    reader consults only the environment; the cache never appears here.

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
