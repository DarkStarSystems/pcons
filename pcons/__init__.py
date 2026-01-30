# SPDX-License-Identifier: MIT
"""
Pcons: A Python-based build system that generates Ninja files.

Pcons is a modern build system inspired by SCons and CMake that uses Python
for build configuration and generates Ninja (or Makefile) build files.
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pcons.core.project import Project

# Re-export commonly used classes for convenient imports
# These imports must be after __version__ is defined but we use noqa to allow it
# Register built-in builders with the BuilderRegistry
# This must happen after core imports but before any user code runs
from pcons.builders import register_builtin_builders  # noqa: E402
from pcons.configure.config import Configure  # noqa: E402
from pcons.core.flags import FlagPair  # noqa: E402
from pcons.core.project import Project  # noqa: E402, F811
from pcons.generators.makefile import MakefileGenerator  # noqa: E402
from pcons.generators.ninja import NinjaGenerator  # noqa: E402
from pcons.generators.xcode import XcodeGenerator  # noqa: E402
from pcons.toolchains import find_c_toolchain  # noqa: E402

register_builtin_builders()

# Import modules namespace to make pcons.modules accessible
from pcons import modules as modules  # noqa: E402, F401

__version__ = "0.7.0"

# Internal storage for CLI variables
_cli_vars: dict[str, str] | None = None

# Global registry for Project instances
_registered_projects: list[Project] = []


def _register_project(project: Project) -> None:
    """Register a project (called by Project.__init__)."""
    _registered_projects.append(project)


def get_registered_projects() -> list[Project]:
    """Get all registered projects."""
    return list(_registered_projects)


def _clear_registered_projects() -> None:
    """Clear the registry (called by CLI before running a script)."""
    _registered_projects.clear()


def get_var(name: str, default: str | None = None) -> str | None:
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


# Valid generator names for CLI and Generator()
GENERATORS = {
    "ninja": NinjaGenerator,
    "make": MakefileGenerator,
    "makefile": MakefileGenerator,  # Alias
    "xcode": XcodeGenerator,
}


def Generator(
    default: str = "ninja",
) -> NinjaGenerator | MakefileGenerator | XcodeGenerator:
    """Get a generator instance based on CLI option or environment.

    The generator can be set with:
        pcons --generator=make
        pcons -G ninja
        pcons -G xcode

    Or when running directly:
        GENERATOR=make python pcons-build.py

    Precedence (highest to lowest):
        1. PCONS_GENERATOR (set by pcons CLI)
        2. GENERATOR environment variable
        3. default parameter

    Args:
        default: Default generator name if not set ("ninja", "make", or "xcode").

    Returns:
        A generator instance (NinjaGenerator, MakefileGenerator, or XcodeGenerator).

    Raises:
        ValueError: If the generator name is not recognized.

    Example:
        from pcons import Project, Generator

        project = Project("myapp")
        # ... configure project ...
        Generator().generate(project)
    """
    name = os.environ.get("PCONS_GENERATOR") or os.environ.get("GENERATOR") or default
    name = name.lower()

    if name not in GENERATORS:
        valid = ", ".join(sorted(set(GENERATORS.keys())))
        raise ValueError(f"Unknown generator '{name}'. Valid options: {valid}")

    return GENERATORS[name]()


# Public API exports
__all__ = [
    # Version
    "__version__",
    # CLI variable access
    "get_var",
    "get_variant",
    # Project registry (for CLI use)
    "get_registered_projects",
    "_register_project",
    "_clear_registered_projects",
    # Core classes
    "Configure",
    "FlagPair",
    "Project",
    # Generators
    "Generator",
    "NinjaGenerator",
    "MakefileGenerator",
    "XcodeGenerator",
    # Toolchain discovery
    "find_c_toolchain",
    # Module system
    "modules",
]
