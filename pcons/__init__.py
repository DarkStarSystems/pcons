# SPDX-License-Identifier: MIT
"""
Pcons: A Python-based build system that generates Ninja files.

Pcons is a modern build system inspired by SCons and CMake that uses Python
for build configuration and generates Ninja (or Makefile) build files.
"""

from __future__ import annotations

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
from pcons.configure.config_file import configure_file  # noqa: E402
from pcons.configure.platform import Platform, get_platform  # noqa: E402
from pcons.core.context import context  # noqa: E402
from pcons.core.flags import FlagPair  # noqa: E402
from pcons.core.preset import (  # noqa: E402
    ToolContribution,
    list_presets,
    preset,
    register_preset,
)
from pcons.core.project import Project  # noqa: E402, F811
from pcons.core.subst import PathToken  # noqa: E402
from pcons.core.test import set_test_properties, set_test_property  # noqa: E402
from pcons.core.vars import get_var, get_variant  # noqa: E402
from pcons.generators.generator import MultiGenerator  # noqa: E402
from pcons.generators.makefile import MakefileGenerator  # noqa: E402
from pcons.generators.metadata import MetadataGenerator  # noqa: E402
from pcons.generators.ninja import NinjaGenerator  # noqa: E402
from pcons.generators.xcode import XcodeGenerator  # noqa: E402
from pcons.packages.description import PackageDescription  # noqa: E402
from pcons.packages.imported import ImportedTarget  # noqa: E402
from pcons.toolchains import (
    find_c_toolchain,
    find_cuda_toolchain,
    find_cython_toolchain,
    find_emscripten_toolchain,
    find_fortran_toolchain,
    find_wasi_toolchain,
)  # noqa: E402
from pcons.tools.install import install_dir  # noqa: E402
from pcons.util.add_subdirectory import add_subdirectory  # noqa: E402

register_builtin_builders()

# Import modules namespace to make pcons.modules accessible
from pcons import modules as modules  # noqa: E402, F401

__version__ = "0.21.0"

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


# Valid generator names for CLI and Generator()
GENERATORS = {
    "ninja": NinjaGenerator,
    "make": MakefileGenerator,
    "makefile": MakefileGenerator,  # Alias
    "metadata": MetadataGenerator,
    "xcode": XcodeGenerator,
}


def Generator(
    default: str = "ninja",
) -> (
    NinjaGenerator
    | MakefileGenerator
    | MetadataGenerator
    | XcodeGenerator
    | MultiGenerator
):
    """Get a generator instance based on CLI option or environment.

    The generator can be set with:
        pcons -G ninja
        pcons -G ninja -G metadata
        pcons -G xcode

    Or when running directly:
        GENERATOR=make python pcons-build.py
        PCONS_GENERATOR=ninja:metadata python pcons-build.py

    Precedence (highest to lowest):
        1. PCONS_GENERATOR (set by pcons CLI)
        2. GENERATOR environment variable
        3. default parameter

    Multiple generators can be specified with colon-separated names
    (e.g., ``PCONS_GENERATOR=ninja:metadata``). Each generator runs
    in order on the same project.

    Args:
        default: Default generator name if not set ("ninja", "make", "metadata", or "xcode").

    Returns:
        A generator instance. When multiple names are given, a MultiGenerator
        that runs each in sequence.

    Raises:
        ValueError: If any generator name is not recognized.

    Example:
        from pcons import Project, Generator

        project = Project("myapp")
        # ... configure project ...
        Generator().generate(project)
    """
    spec = os.environ.get("PCONS_GENERATOR") or os.environ.get("GENERATOR") or default
    names = [n.strip().lower() for n in spec.split(":") if n.strip()]

    valid = ", ".join(sorted(set(GENERATORS.keys())))
    for name in names:
        if name not in GENERATORS:
            raise ValueError(f"Unknown generator '{name}'. Valid options: {valid}")

    instances = [GENERATORS[name]() for name in names]
    if len(instances) == 1:
        return instances[0]
    return MultiGenerator(instances)


# Public API exports
__all__ = [
    # Version
    "__version__",
    # CLI variable access
    "get_var",
    "get_variant",
    # Install helpers
    "install_dir",
    # Project registry (for CLI use)
    "get_registered_projects",
    "_register_project",
    "_clear_registered_projects",
    # Core classes
    "Configure",
    "configure_file",
    "FlagPair",
    "PathToken",
    "Platform",
    "get_platform",
    "ImportedTarget",
    "PackageDescription",
    "Project",
    # Presets (contributed-preset registry)
    "register_preset",
    "preset",
    "list_presets",
    "ToolContribution",
    # Generators
    # Intentionally not exposing MultiGenerator as it's an implementation detail
    "Generator",
    "NinjaGenerator",
    "MakefileGenerator",
    "MetadataGenerator",
    "XcodeGenerator",
    # Test helpers
    "set_test_property",
    "set_test_properties",
    # Toolchain discovery
    "find_c_toolchain",
    "find_cuda_toolchain",
    "find_cython_toolchain",
    "find_emscripten_toolchain",
    "find_fortran_toolchain",
    "find_wasi_toolchain",
    # Module system
    "modules",
    # Misc utilities
    "context",
    "add_subdirectory",
]
