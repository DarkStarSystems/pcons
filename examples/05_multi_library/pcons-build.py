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

import os
import sys
from pathlib import Path

from pcons import Generator, Project, find_c_toolchain
from pcons.generators.compile_commands import CompileCommandsGenerator
from pcons.generators.mermaid import MermaidGenerator

# =============================================================================
# Build Script
# =============================================================================

build_dir = Path(os.environ.get("PCONS_BUILD_DIR", "build"))
src_dir = Path(__file__).parent / "src"
include_dir = Path(__file__).parent / "include"

# Find a C toolchain (uses platform-appropriate defaults)
toolchain = find_c_toolchain()
project = Project("multi_library", build_dir=build_dir)
env = project.Environment(toolchain=toolchain)

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

# -----------------------------------------------------------------------------
# Resolve and Generate
# -----------------------------------------------------------------------------
project.resolve()

# Generate build file
generator = Generator()
generator.generate(project)

# Generate Mermaid dependency diagram
mermaid_gen = MermaidGenerator(direction="LR")
mermaid_gen.generate(project)

# Generate compile_commands.json for IDE integration
cc_gen = CompileCommandsGenerator()
cc_gen.generate(project)

print(f"Generated {build_dir / 'build.ninja'}")
print(f"Generated {build_dir / 'compile_commands.json'}")
print(f"Generated {build_dir / 'deps.mmd'}")
