#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build script for a multi-file C project.

This example demonstrates the target-centric build API:
- Compiling multiple source files into a single program
- Using include directories via private requirements
- Automatic resolution of sources to objects
"""

from pathlib import Path
import os

from pcons.core.project import Project
from pcons.generators.ninja import NinjaGenerator
from pcons.toolchains import find_c_toolchain

# =============================================================================
# Build Script
# =============================================================================

# Directories
build_dir = Path(os.environ.get("PCONS_BUILD_DIR", "build"))
src_dir = Path(__file__).parent / "src"
include_dir = Path(__file__).parent / "include"

# Find a C toolchain (tries clang, gcc, msvc in order)
toolchain = find_c_toolchain()

# Create project with the toolchain
project = Project("multi_file", build_dir=build_dir)
env = project.Environment(toolchain=toolchain)

# Create calculator program target using target-centric API
calculator = project.Program("calculator", env)
calculator.sources = [
    project.node(src_dir / "math_ops.c"),
    project.node(src_dir / "main.c"),
]
calculator.private.include_dirs.append(include_dir)
calculator.private.compile_flags.extend(["-Wall", "-Wextra"])

# Resolve targets (computes effective requirements, creates nodes)
project.resolve()

# Generate ninja build file
generator = NinjaGenerator()
generator.generate(project, build_dir)

print(f"Generated {build_dir / 'build.ninja'}")
