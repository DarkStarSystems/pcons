# SPDX-License-Identifier: MIT
"""Debug/trace system for pcons internals.

Provides selective tracing of specific subsystems via --debug=<subsystem>
CLI flags or PCONS_DEBUG=<subsystems> environment variable.

Available subsystems:
    configure - Tool detection, feature checks, configuration caching
    resolve   - Target resolution, object node creation, dependency propagation
    generate  - Build file writing, rule creation, path handling
    subst     - Variable substitution, function calls, token expansion
    env       - Environment creation, tool setup, variable lookups
    deps      - Dependency graph, effective requirements propagation
    all       - Enable all subsystems

Usage:
    pcons --debug=resolve,subst
    PCONS_DEBUG=all pcons
"""

from __future__ import annotations

import logging
import os
from typing import Any

# Global state
_enabled_subsystems: set[str] = set()
_initialized = False

SUBSYSTEMS = frozenset(
    {"configure", "resolve", "generate", "subst", "env", "deps", "all"}
)


def init_debug(debug_spec: str | None = None) -> None:
    """Initialize debug subsystems from --debug flag or PCONS_DEBUG env var.

    Args:
        debug_spec: Comma-separated list of subsystem names (e.g., "resolve,subst").
                   If None, reads from PCONS_DEBUG environment variable.

    Example:
        init_debug("resolve,subst")  # Enable resolve and subst tracing
        init_debug("all")            # Enable all tracing
        init_debug()                 # Read from PCONS_DEBUG env var
    """
    global _enabled_subsystems, _initialized

    spec = debug_spec or os.environ.get("PCONS_DEBUG", "")
    if not spec:
        _enabled_subsystems = set()
        _initialized = True
        return

    parts = [p.strip().lower() for p in spec.split(",") if p.strip()]
    if "all" in parts:
        _enabled_subsystems = set(SUBSYSTEMS - {"all"})
    else:
        # Only include valid subsystem names
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
    """Check if a subsystem has tracing enabled.

    Args:
        subsystem: Subsystem name (e.g., "resolve", "subst").

    Returns:
        True if tracing is enabled for this subsystem.
    """
    return subsystem in _enabled_subsystems


def trace(subsystem: str, message: str, *args: Any, **kwargs: Any) -> None:
    """Log a trace message if subsystem is enabled.

    Uses Python's logging at DEBUG level with a pcons.<subsystem> logger.

    Args:
        subsystem: Subsystem name.
        message: Log message (can contain %s format specifiers).
        *args: Format arguments for the message.
        **kwargs: Additional keyword arguments for the logger.

    Example:
        trace("resolve", "Resolving target: %s", target.name)
    """
    if subsystem in _enabled_subsystems:
        logger = logging.getLogger(f"pcons.{subsystem}")
        logger.debug(message, *args, **kwargs)


def trace_value(subsystem: str, name: str, value: object) -> None:
    """Log a named value if subsystem is enabled.

    Convenience function for tracing variable values with consistent indentation.

    Args:
        subsystem: Subsystem name.
        name: Variable/attribute name being traced.
        value: The value to log.

    Example:
        trace_value("resolve", "sources", [str(s.path) for s in target.sources])
    """
    if subsystem in _enabled_subsystems:
        logger = logging.getLogger(f"pcons.{subsystem}")
        logger.debug("    %s = %s", name, value)
