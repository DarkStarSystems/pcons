#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build script for a multi-file C project.

This example demonstrates the target-centric build API:
- Compiling multiple source files into a single program
- Using include directories via private requirements
- Automatic resolution of sources to objects
"""

from pcons import Project

# =============================================================================
# Build Script
# =============================================================================

# Create project
project = Project("multi_file")

# Directories
src_dir = project.root_dir / "src"
include_dir = project.root_dir / "include"
env = project.Environment(toolchain="c")
# Warning flags, resolved per-toolchain (/W4 on MSVC, -Wall … on GCC/Clang).
env.apply_preset("warnings")

# Create calculator program target using target-centric API
calculator = project.Program("calculator", env)
calculator.add_sources([src_dir / "math_ops.c", src_dir / "main.c"])
calculator.private.include_dirs.append(include_dir)
