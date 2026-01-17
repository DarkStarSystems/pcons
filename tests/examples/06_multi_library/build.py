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

from pathlib import Path
import os

from pcons.core.project import Project
from pcons.generators.ninja import NinjaGenerator
from pcons.generators.mermaid import MermaidGenerator
from pcons.toolchains import find_c_toolchain

# =============================================================================
# Build Script
# =============================================================================

build_dir = Path(os.environ.get("PCONS_BUILD_DIR", "build"))
src_dir = Path(__file__).parent / "src"
include_dir = Path(__file__).parent / "include"

toolchain = find_c_toolchain()
project = Project("multi_library", build_dir=build_dir)
env = project.Environment(toolchain=toolchain)

# -----------------------------------------------------------------------------
# Library: libmath - low-level math utilities
# -----------------------------------------------------------------------------
libmath = project.StaticLibrary("math", env)
libmath.sources = [project.node(src_dir / "math_utils.c")]
# Public includes propagate to consumers
libmath.public.include_dirs.append(include_dir)

# -----------------------------------------------------------------------------
# Library: libphysics - physics simulation, depends on libmath
# -----------------------------------------------------------------------------
libphysics = project.StaticLibrary("physics", env)
libphysics.sources = [project.node(src_dir / "physics.c")]
libphysics.link(libmath)  # Gets libmath's public includes transitively

# -----------------------------------------------------------------------------
# Program: simulator - main application
# -----------------------------------------------------------------------------
simulator = project.Program("simulator", env)
simulator.sources = [project.node(src_dir / "main.c")]
simulator.link(libphysics)  # Gets both libphysics and libmath includes

# -----------------------------------------------------------------------------
# Resolve and Generate
# -----------------------------------------------------------------------------
project.resolve()

# Generate ninja build file
ninja_gen = NinjaGenerator()
ninja_gen.generate(project, build_dir)

# Generate Mermaid dependency diagram
mermaid_gen = MermaidGenerator(direction="LR")
mermaid_gen.generate(project, build_dir)

print(f"Generated {build_dir / 'build.ninja'}")
print(f"Generated {build_dir / 'deps.mmd'}")
print()
print("Dependency graph (Mermaid):")
print("-" * 40)
print((build_dir / "deps.mmd").read_text())
