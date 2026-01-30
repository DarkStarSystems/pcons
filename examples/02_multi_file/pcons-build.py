#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build script for a multi-file C project.

This example demonstrates the target-centric build API:
- Compiling multiple source files into a single program
- Using include directories via private requirements
- Automatic resolution of sources to objects
"""

import os
from pathlib import Path

from pcons import Generator, Project, find_c_toolchain

# =============================================================================
# Build Script
# =============================================================================

# Directories
build_dir = Path(os.environ.get("PCONS_BUILD_DIR", "build"))
src_dir = Path(__file__).parent / "src"
include_dir = Path(__file__).parent / "include"

# Find a C toolchain (uses platform-appropriate defaults)
toolchain = find_c_toolchain()

# Create project with the toolchain
project = Project("multi_file", build_dir=build_dir)
env = project.Environment(toolchain=toolchain)

# Create calculator program target using target-centric API
calculator = project.Program("calculator", env)
calculator.add_sources([src_dir / "math_ops.c", src_dir / "main.c"])
calculator.private.include_dirs.append(include_dir)

# Add warning flags appropriate for the toolchain
if toolchain.name in ("msvc", "clang-cl"):
    calculator.private.compile_flags.extend(["/W4"])
else:
    calculator.private.compile_flags.extend(["-Wall", "-Wextra"])

# Resolve targets (computes effective requirements, creates nodes)
project.resolve()

# Generate build file
generator = Generator()
generator.generate(project)

print(f"Generated {build_dir}")
