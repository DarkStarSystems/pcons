#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build script for a multi-file C project.

This example demonstrates the target-centric build API:
- Compiling multiple source files into a single program
- Using include directories via private requirements
- Automatic resolution of sources to objects
"""

from pcons import Project, find_c_toolchain

# =============================================================================
# Build Script
# =============================================================================

# Find a C toolchain (uses platform-appropriate defaults)
toolchain = find_c_toolchain()

# Create project with the toolchain
project = Project("multi_file")

# Directories
src_dir = project.root_dir / "src"
include_dir = project.root_dir / "include"
build_dir = project.build_dir
env = project.Environment(toolchain=toolchain)
# Warning flags, resolved per-toolchain (/W4 on MSVC, -Wall … on GCC/Clang).
env.apply_preset("warnings")

# Create calculator program target using target-centric API
calculator = project.Program("calculator", env)
calculator.add_sources([src_dir / "math_ops.c", src_dir / "main.c"])
calculator.private.include_dirs.append(include_dir)

project.generate()

print(f"Generated {build_dir}")
