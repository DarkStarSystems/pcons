# SPDX-License-Identifier: MIT
"""Debug/trace system for pcons internals.

Provides selective tracing of specific subsystems via --debug=<subsystem>
CLI flags or PCONS_DEBUG=<subsystems> environment variable.

Use --debug=help to list available subsystems.

Usage:
    pcons --debug=resolve,subst
    pcons --debug=help
    PCONS_DEBUG=all pcons
"""

from __future__ import annotations

import logging
import os
from typing import Any

# Global state
_enabled_subsystems: set[str] = set()
_initialized = False

# Subsystem descriptions — single source of truth for --debug help and --debug=help
SUBSYSTEM_DESCRIPTIONS: dict[str, str] = {
    "configure": "Tool detection, feature checks, compiler probes",
    "resolve": "Target resolution, object nodes, dependency propagation",
    "generate": "Build file writing, rule creation, path handling",
    "subst": "Variable substitution, function calls, token expansion",
    "env": "Environment creation, tool setup, variable lookups",
    "deps": "Dependency graph, effective requirements propagation",
}

SUBSYSTEMS = frozenset(set(SUBSYSTEM_DESCRIPTIONS.keys()) | {"all"})


class UnknownSubsystemsError(ValueError):
    """A debug spec named subsystems that do not exist."""

    def __init__(self, unknown: set[str]) -> None:
        self.unknown = frozenset(unknown)
        super().__init__(
            f"Unknown debug subsystem(s): {', '.join(sorted(self.unknown))}"
        )


class SubsystemListRequested(Exception):
    """The spec was ``help``: list the subsystems rather than enabling any."""


def print_subsystems(file: Any = None) -> None:
    """Print available debug subsystems and their descriptions."""
    import sys

    out = file or sys.stdout
    print("Available debug subsystems (--debug=SUBSYSTEM[,SUBSYSTEM,...]):", file=out)
    width = max(len(name) for name in SUBSYSTEM_DESCRIPTIONS)
    for name, desc in SUBSYSTEM_DESCRIPTIONS.items():
        print(f"  {name:<{width}}  {desc}", file=out)
    print(f"  {'all':<{width}}  Enable all subsystems", file=out)
    print(f"  {'help':<{width}}  Show this list", file=out)


def init_debug(debug_spec: str | None = None) -> None:
    """Initialize debug subsystems from --debug flag or PCONS_DEBUG env var.

    Args:
        debug_spec: Comma-separated list of subsystem names (e.g., "resolve,subst").
                   If None, reads from PCONS_DEBUG environment variable.

    Example:
        init_debug("resolve,subst")  # Enable resolve and subst tracing
        init_debug("all")            # Enable all tracing
        init_debug()                 # Read from PCONS_DEBUG env var

    Raises:
        UnknownSubsystemsError: A name in the spec is not a subsystem.
        SubsystemListRequested: The spec was "help". The caller decides how to
            render `print_subsystems` and what to exit with.
    """
    global _enabled_subsystems, _initialized

    spec = debug_spec or os.environ.get("PCONS_DEBUG", "")
    if not spec:
        _enabled_subsystems = set()
        _initialized = True
        return

    parts = [p.strip().lower() for p in spec.split(",") if p.strip()]

    if "help" in parts:
        raise SubsystemListRequested

    if "all" in parts:
        _enabled_subsystems = set(SUBSYSTEMS - {"all"})
    else:
        unknown = set(parts) - SUBSYSTEMS
        if unknown:
            raise UnknownSubsystemsError(unknown)
        _enabled_subsystems = set(parts) & SUBSYSTEMS

    _initialized = True

    # Set up subsystem-specific loggers at DEBUG level
    for subsystem in _enabled_subsystems:
        logger = logging.getLogger(f"pcons.{subsystem}")
        logger.setLevel(logging.DEBUG)


def reset_debug() -> None:
    """Reset debug state (primarily for testing)."""
    global _enabled_subsystems, _initialized
    _enabled_subsystems = set()
    _initialized = False


def is_enabled(subsystem: str) -> bool:
    """Check if a subsystem has tracing enabled."""
    return subsystem in _enabled_subsystems


def trace(subsystem: str, message: str, *args: Any, **kwargs: Any) -> None:
    """Log a trace message (logging DEBUG on pcons.<subsystem>) if enabled.

    Example:
        trace("resolve", "Resolving target: %s", target.name)
    """
    if subsystem in _enabled_subsystems:
        logger = logging.getLogger(f"pcons.{subsystem}")
        logger.debug(message, *args, **kwargs)


def trace_value(subsystem: str, name: str, value: object) -> None:
    """Log a named value, indented, if the subsystem is enabled."""
    if subsystem in _enabled_subsystems:
        logger = logging.getLogger(f"pcons.{subsystem}")
        logger.debug("    %s = %s", name, value)
