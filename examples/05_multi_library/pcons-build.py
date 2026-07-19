#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Example demonstrating multi-library builds with dependency visualization.

This example shows:
- Multiple static libraries with dependencies
- Transitive include directory propagation
- Mermaid diagram generation for dependency visualization

Build graph:
    libmath <-- libphysics <-- simulator
"""

import sys

from pcons import Project
from pcons.generators.dot import DotGenerator
from pcons.generators.mermaid import MermaidGenerator

# =============================================================================
# Build Script
# =============================================================================

project = Project("multi_library")
env = project.Environment(toolchain="c")

# -----------------------------------------------------------------------------
# Library: libmath - low-level math utilities
# -----------------------------------------------------------------------------
libmath = project.StaticLibrary("math", env)
libmath.add_sources(["src/math_utils.c"])
# Public includes propagate to consumers
libmath.public.include_dirs.append("include")
# Link against libm for math functions (required on Linux, not needed on Windows)
if sys.platform != "win32":
    libmath.link("m")

# -----------------------------------------------------------------------------
# Library: libphysics - physics simulation, depends on libmath
# -----------------------------------------------------------------------------
libphysics = project.SharedLibrary("physics", env)
libphysics.add_sources(["src/physics.c"])
if sys.platform == "win32":
    # export symbols on Windows
    libphysics.private.defines.append("PHYSICS_BUILDING_DLL")
libphysics.link(libmath)  # Gets libmath's public includes transitively

# -----------------------------------------------------------------------------
# Program: simulator - main application
# -----------------------------------------------------------------------------
simulator = project.Program("simulator", env)
simulator.add_sources(["src/main.c"])
simulator.link_private(libphysics)  # Gets both libphysics and libmath includes

# Also generate dependency diagrams alongside the build files
mermaid_gen = MermaidGenerator(direction="LR")
mermaid_gen.generate(project)
dot_gen = DotGenerator(rankdir="LR")
dot_gen.generate(project)
