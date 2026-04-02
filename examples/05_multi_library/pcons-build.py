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

from pcons import Project, find_c_toolchain
from pcons.generators.mermaid import MermaidGenerator

# =============================================================================
# Build Script
# =============================================================================

project = Project("multi_library")

src_dir = project.root_dir / "src"
include_dir = project.root_dir / "include"
build_dir = project.build_dir
env = project.Environment(toolchain=find_c_toolchain())

# -----------------------------------------------------------------------------
# Library: libmath - low-level math utilities
# -----------------------------------------------------------------------------
libmath = project.StaticLibrary("math", env)
libmath.add_sources([src_dir / "math_utils.c"])
# Public includes propagate to consumers
libmath.public.include_dirs.append(include_dir)
# Link against libm for math functions (required on Linux, not needed on Windows)
if sys.platform != "win32":
    libmath.public.link_libs.append("m")

# -----------------------------------------------------------------------------
# Library: libphysics - physics simulation, depends on libmath
# -----------------------------------------------------------------------------
libphysics = project.StaticLibrary("physics", env)
libphysics.add_sources([src_dir / "physics.c"])
libphysics.link(libmath)  # Gets libmath's public includes transitively

# -----------------------------------------------------------------------------
# Program: simulator - main application
# -----------------------------------------------------------------------------
simulator = project.Program("simulator", env)
simulator.add_sources([src_dir / "main.c"])
simulator.link(libphysics)  # Gets both libphysics and libmath includes

project.generate()

# Generate Mermaid dependency diagram (after generate, which auto-resolves)
mermaid_gen = MermaidGenerator(direction="LR")
mermaid_gen.generate(project)

print(f"Generated {build_dir / 'build.ninja'}")
print(f"Generated {build_dir / 'compile_commands.json'}")
print(f"Generated {build_dir / 'deps.mmd'}")
