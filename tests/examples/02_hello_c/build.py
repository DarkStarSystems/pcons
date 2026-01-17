#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build script for a simple C program.

This example demonstrates the target-centric build API:
- Using find_c_toolchain() to automatically select a compiler
- Creating a Program target with sources
- Using private requirements for compile flags
- Automatic resolution and generation
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

# Find a C toolchain (tries clang, gcc, msvc in order)
toolchain = find_c_toolchain()

# Create project with the toolchain
project = Project("hello_c", build_dir=build_dir)
env = project.Environment(toolchain=toolchain)

# Create program target using the target-centric API
hello = project.Program("hello", env)
hello.sources = [project.node(src_dir / "hello.c")]
hello.private.compile_flags.extend(["-Wall", "-Wextra"])

# Resolve targets (computes effective requirements, creates nodes)
project.resolve()

# Generate ninja build file
generator = NinjaGenerator()
generator.generate(project, build_dir)

print(f"Generated {build_dir / 'build.ninja'}")
